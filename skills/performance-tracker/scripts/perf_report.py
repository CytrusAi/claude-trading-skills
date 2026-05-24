#!/usr/bin/env python3
"""
perf_report.py — one-command performance tearsheet for the options campaign.

Pulls the account equity curve from Alpaca, converts it to daily returns, fetches
SPY over the same window as a benchmark (from the same Alpaca data feed — no
yfinance), and renders a quantstats HTML tearsheet into reports/. Also prints a
concise text summary to stdout so it's useful without opening the browser.

The same machinery works on a backtest: feed it a returns/equity CSV with --csv
and compare backtest vs live by eyeballing two reports (Sharpe drift, drawdown,
benchmark spread).

Examples:
  python3 scripts/perf_report.py                      # last 3M, vs SPY, open in browser
  python3 scripts/perf_report.py --period 1A          # last year
  python3 scripts/perf_report.py --period all --no-open
  python3 scripts/perf_report.py --csv backtest.csv --title "MPC backtest"
  python3 scripts/perf_report.py --no-benchmark

CSV format for --csv: first column = date; plus a column named either
'equity' (account value, converted to returns) or 'returns' (already %/decimal).

Env keys (from ~/.zshrc): ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_BASE_URL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import warnings

warnings.filterwarnings("ignore")

# Headless-safe plotting (works under cron / no display) — must precede quantstats.
import matplotlib

matplotlib.use("Agg")

try:
    import certifi

    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

import pandas as pd
import quantstats as qs

ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SEC = os.environ.get("ALPACA_API_SECRET", "")
ALPACA_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Campaign root: $CAMPAIGN_DIR if set, else the current working directory.
# Keeps the skill portable (no hard-coded home paths).
BASE_DIR = os.environ.get("CAMPAIGN_DIR", os.getcwd())
REPORTS_DIR = f"{BASE_DIR}/reports"
RUN_LOG = f"{REPORTS_DIR}/_run_log.txt"
ET = "America/New_York"


# ============================================================
# HTTP helpers (mirrors thursday_execute.py)
# ============================================================
def http_get(url: str, headers: dict, timeout: int = 20) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"_error": str(e)}
        return e.code, body
    except Exception as e:
        return 0, {"_error": str(e)}


def _alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SEC}


def alpaca_trading(path: str, params: dict | None = None) -> tuple[int, dict]:
    url = f"{ALPACA_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http_get(url, _alpaca_headers())


def alpaca_data(path: str, params: dict | None = None) -> tuple[int, dict]:
    url = f"https://data.alpaca.markets{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http_get(url, _alpaca_headers())


# ============================================================
# Returns builders
# ============================================================
def _to_et_dates(ts_seconds) -> pd.DatetimeIndex:
    """Unix seconds -> tz-naive ET calendar dates (one per trading day)."""
    return (
        pd.to_datetime(ts_seconds, unit="s", utc=True).tz_convert(ET).tz_localize(None).normalize()
    )


def get_portfolio_returns(period: str) -> pd.Series:
    """Account equity curve from Alpaca -> daily returns Series."""
    code, d = alpaca_trading(
        "/v2/account/portfolio/history",
        {"period": period, "timeframe": "1D", "extended_hours": "false"},
    )
    if code != 200:
        raise RuntimeError(f"Alpaca portfolio/history failed ({code}): {d}")
    ts, eq = d.get("timestamp", []), d.get("equity", [])
    if not ts or not eq:
        raise RuntimeError("Alpaca returned an empty equity curve for this period.")
    s = pd.Series(eq, index=_to_et_dates(ts), dtype="float64")
    s = s[s > 0].dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    rets = s.pct_change().dropna()
    rets.name = "Campaign"
    if len(rets) < 2:
        raise RuntimeError(f"Only {len(rets)} return(s) in this window — try a longer --period.")
    return rets


def get_spy_returns(start: dt.date, end: dt.date) -> pd.Series | None:
    """SPY daily closes from Alpaca over [start, end] -> daily returns Series."""
    closes: dict = {}
    page = None
    while True:
        params = {
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
            "adjustment": "all",
        }
        if page:
            params["page_token"] = page
        code, d = alpaca_data("/v2/stocks/SPY/bars", params)
        if code != 200:
            print(f"  ! SPY benchmark fetch failed ({code}) — continuing without it.")
            return None
        for b in d.get("bars", []):
            day = pd.to_datetime(b["t"], utc=True).tz_convert(ET).tz_localize(None).normalize()
            closes[day] = float(b["c"])
        page = d.get("next_page_token")
        if not page:
            break
    if len(closes) < 2:
        return None
    s = pd.Series(closes).sort_index()
    rets = s.pct_change().dropna()
    rets.name = "SPY"
    return rets


def load_csv_returns(path: str) -> pd.Series:
    """CSV (date + 'equity' or 'returns' column) -> daily returns Series."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    if "returns" in df.columns:
        rets = df["returns"].astype("float64").dropna()
    elif "equity" in df.columns:
        rets = df["equity"].astype("float64").pct_change().dropna()
    else:
        rets = df[df.columns[0]].astype("float64").dropna()  # assume 2nd col = returns
    rets.name = "Backtest"
    if len(rets) < 2:
        raise RuntimeError("CSV produced fewer than 2 returns.")
    return rets


# ============================================================
# Reporting
# ============================================================
def _pct(x) -> str:
    try:
        return f"{float(x) * 100:.2f}%"
    except Exception:
        return "n/a"


def print_summary(rets: pd.Series, bench: pd.Series | None, label: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {label}  ({rets.index[0].date()} → {rets.index[-1].date()}, {len(rets)} days)")
    print("=" * 60)
    rows = [
        ("Total return", _pct(qs.stats.comp(rets))),
        ("CAGR", _pct(qs.stats.cagr(rets))),
        ("Sharpe", f"{qs.stats.sharpe(rets):.2f}"),
        ("Sortino", f"{qs.stats.sortino(rets):.2f}"),
        ("Max drawdown", _pct(qs.stats.max_drawdown(rets))),
        ("Volatility (ann.)", _pct(qs.stats.volatility(rets))),
        ("Win rate", _pct(qs.stats.win_rate(rets))),
    ]
    for k, v in rows:
        print(f"  {k:<20} {v:>12}")
    if bench is not None and len(bench) >= 2:
        common = rets.index.intersection(bench.index)
        if len(common) >= 2:
            me = qs.stats.comp(rets.loc[common])
            spy = qs.stats.comp(bench.loc[common])
            print("-" * 60)
            print(f"  {'You vs SPY':<20} {_pct(me):>12}  vs {_pct(spy)}")
            print(f"  {'Excess return':<20} {_pct(me - spy):>12}")
    print("=" * 60)


def append_run_log(line: str) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(RUN_LOG, "a") as f:
        f.write(line + "\n")


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="Campaign performance tearsheet (quantstats).")
    ap.add_argument(
        "--period",
        default="3M",
        help="Alpaca window: 1M, 3M, 6M, 1A, all (default 3M). Ignored with --csv.",
    )
    ap.add_argument("--csv", help="Use a returns/equity CSV instead of the live Alpaca account.")
    ap.add_argument("--no-benchmark", action="store_true", help="Skip the SPY comparison.")
    ap.add_argument("--title", help="Report title (default auto).")
    ap.add_argument("--output", help="Output HTML path (default reports/perf_<timestamp>.html).")
    ap.add_argument("--open", dest="open_", action="store_true", help="Open the report when done.")
    ap.add_argument("--no-open", dest="open_", action="store_false", help="Do not open the report.")
    ap.set_defaults(open_=None)
    args = ap.parse_args()

    if not args.csv and not (ALPACA_KEY and ALPACA_SEC):
        print("ERROR: ALPACA_API_KEY / ALPACA_API_SECRET not set in environment.")
        return 1

    # Build returns
    if args.csv:
        if not os.path.exists(args.csv):
            print(f"ERROR: CSV not found: {args.csv}")
            return 1
        rets = load_csv_returns(args.csv)
        label = args.title or f"Backtest — {os.path.basename(args.csv)}"
        slug = "backtest"
    else:
        print(f"Fetching account equity curve from Alpaca (period={args.period})...")
        rets = get_portfolio_returns(args.period)
        label = args.title or f"Options Campaign — last {args.period}"
        slug = f"perf_{args.period}"

    # Benchmark
    bench = None
    if not args.no_benchmark:
        bench = get_spy_returns(rets.index[0].date(), rets.index[-1].date())

    # Text summary (always)
    print_summary(rets, bench, label)

    # HTML tearsheet
    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = args.output or f"{REPORTS_DIR}/{slug}_{ts}.html"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f"\nRendering tearsheet → {out}")
    qs.reports.html(
        rets,
        benchmark=bench if (bench is not None and len(bench) >= 2) else None,
        output=out,
        title=label,
        benchmark_title="SPY" if bench is not None else None,
    )
    append_run_log(
        f"{dt.datetime.now().isoformat(timespec='seconds')}  perf_report  {label}  -> {out}"
    )
    print(f"Saved: {out}")

    # Open (default: only when run interactively)
    do_open = args.open_ if args.open_ is not None else sys.stdout.isatty()
    if do_open:
        try:
            subprocess.run(["open", out], check=False)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
