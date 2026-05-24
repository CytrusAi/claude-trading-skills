#!/usr/bin/env python3
"""
trade_ledger.py — turn the campaign's thesis bookkeeping into a TRADE-LEVEL
returns series, so performance is measured on DEPLOYED CAPITAL (the strategy
edge) instead of the cash-diluted account.

Why this exists
---------------
perf_report.py measures the whole Alpaca account (mostly cash → tiny vol, noisy
Sharpe). This script measures the trades themselves: per-trade return on the
capital you actually put at risk. That's the number that tells you if the edge
is working.

Reads
-----
  state/theses/*.yaml        — entry plan, position_target, status_history, exit
  state/theses/_index.json   — thesis roster + statuses
  Alpaca /v2/positions       — live option legs → unrealized P&L on the open book

Writes (reports/)
-----------------
  trade_ledger_<date>.csv    — every thesis: status, capital at risk, premium,
                               realized/unrealized P&L, return on risk
  trade_returns_<date>.csv   — CLOSED trades only: date,returns (decimal R)
                               → feeds straight into perf_report.py --csv

Chaining
--------
  With --report and >=2 CLOSED trades, calls perf_report.py --csv on the returns
  file to render a tearsheet of the STRATEGY (not the account).

Return convention
-----------------
  return_on_risk = realized_pnl / capital_at_risk
  (+0.50 = made half of what you risked; -1.00 = lost the full risk.)

Exit schema — add this block to a thesis when it closes, and set status: CLOSED
-------------------------------------------------------------------------------
  status: CLOSED
  exit:
    exit_date: 2026-05-26
    realized_pnl: -250.0        # net $ across ALL spreads (+ = gain)
    capital_at_risk: 930.0      # optional; else falls back to position_target.max_loss
    note: "Closed combo 4ee22148 at $6.80 credit vs $9.30 debit"

Usage
-----
  python3 scripts/trade_ledger.py                 # ledger + summary, live open book
  python3 scripts/trade_ledger.py --report        # also render tearsheet if >=2 closed
  python3 scripts/trade_ledger.py --no-positions  # skip Alpaca (offline ledger only)

Env keys (from ~/.zshrc): ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_BASE_URL.
Run under login zsh so keys load:
  /bin/zsh -lc 'source ~/.zshrc && cd ~/Desktop/trading_campaign && python3 scripts/trade_ledger.py'
"""

import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

try:
    import certifi

    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

# Campaign root: $CAMPAIGN_DIR if set, else the current working directory.
# Keeps the skill portable (no hard-coded home paths).
BASE_DIR = os.environ.get("CAMPAIGN_DIR", os.getcwd())
THESES_DIR = f"{BASE_DIR}/state/theses"
REPORTS_DIR = f"{BASE_DIR}/reports"
RUN_LOG = f"{REPORTS_DIR}/_run_log.txt"

ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SEC = os.environ.get("ALPACA_API_SECRET", "")
ALPACA_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# OCC option symbol: ROOT + YYMMDD + C/P + strike(8). e.g. GOOGL260717C00400000
OCC_RE = re.compile(r"^([A-Z]+)\d{6}[CP]\d{8}$")

# Statuses that represent capital actually deployed (a real position).
OPEN_STATUSES = {"ACTIVE", "CLOSING"}
DEAD_STATUSES = {"EXPIRED_IDEA", "IDEA", "ENTRY_READY"}  # never deployed (yet)


# ============================================================
# HTTP (mirrors perf_report.py / thursday_execute.py)
# ============================================================
def http_get(url: str, headers: dict, timeout: int = 20) -> tuple[int, object]:
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


def alpaca_positions() -> list[dict]:
    """Live positions from Alpaca. Returns [] on any failure (graceful)."""
    if not (ALPACA_KEY and ALPACA_SEC):
        print("  ! Alpaca keys not set — skipping live open-book P&L.")
        return []
    code, d = http_get(
        f"{ALPACA_URL}/v2/positions",
        {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SEC},
    )
    if code != 200 or not isinstance(d, list):
        print(f"  ! Alpaca /v2/positions failed ({code}) — open-book P&L skipped.")
        return []
    return d


def option_pnl_by_underlying(positions: list[dict]) -> dict:
    """Sum unrealized P&L across option legs, grouped by underlying ticker."""
    out: dict = {}
    for p in positions:
        sym = p.get("symbol", "")
        m = OCC_RE.match(sym)
        if not m:
            continue  # equity / non-option leg
        root = m.group(1)
        rec = out.setdefault(
            root, {"unrealized_pl": 0.0, "market_value": 0.0, "cost_basis": 0.0, "legs": 0}
        )
        rec["unrealized_pl"] += float(p.get("unrealized_pl") or 0)
        rec["market_value"] += float(p.get("market_value") or 0)
        rec["cost_basis"] += float(p.get("cost_basis") or 0)
        rec["legs"] += 1
    return out


# ============================================================
# Thesis parsing
# ============================================================
def load_theses() -> list[dict]:
    theses = []
    for path in sorted(glob.glob(f"{THESES_DIR}/th_*.yaml")):
        try:
            with open(path) as f:
                t = yaml.safe_load(f) or {}
            t["_path"] = path
            theses.append(t)
        except Exception as e:
            print(f"  ! could not parse {os.path.basename(path)}: {e}")
    return theses


def _entry_date(t: dict) -> Optional[str]:
    """First time the thesis went ACTIVE (capital deployed); else created_at."""
    for h in t.get("status_history", []) or []:
        if h.get("status") == "ACTIVE":
            at = str(h.get("at", ""))[:10]
            if at:
                return at
    return str(t.get("created_at", ""))[:10] or None


def _capital_at_risk(t: dict) -> Optional[float]:
    ex = t.get("exit") or {}
    if ex.get("capital_at_risk") is not None:
        return float(ex["capital_at_risk"])
    pt = t.get("position_target") or {}
    if pt.get("max_loss") is not None:
        return float(pt["max_loss"])
    return None


def _net_premium(t: dict) -> Optional[float]:
    """Total premium at entry (credit +, debit -). Best-effort from position_target."""
    pt = t.get("position_target") or {}
    if pt.get("credit_received") is not None:
        return float(pt["credit_received"])  # already total $, credit positive
    if pt.get("net_debit") is not None and pt.get("spreads") is not None:
        return -float(pt["net_debit"]) * 100 * float(pt["spreads"])  # debit negative
    return None


def build_ledger(theses: list[dict], opl: dict) -> list[dict]:
    rows = []
    for t in theses:
        status = t.get("status", "UNKNOWN")
        ticker = t.get("ticker", "?")
        risk = _capital_at_risk(t)
        ex = t.get("exit") or {}
        realized = float(ex["realized_pnl"]) if ex.get("realized_pnl") is not None else None
        exit_date = str(ex.get("exit_date", ""))[:10] or None

        # Live unrealized P&L for open positions (matched by underlying ticker).
        live = opl.get(ticker, {})
        unrealized = live.get("unrealized_pl") if status in OPEN_STATUSES else None

        ror_realized = (realized / risk) if (realized is not None and risk) else None
        ror_unreal = (unrealized / risk) if (unrealized is not None and risk) else None

        rows.append(
            {
                "thesis_id": t.get("thesis_id", os.path.basename(t.get("_path", ""))),
                "ticker": ticker,
                "type": t.get("thesis_type", ""),
                "status": status,
                "entry_date": _entry_date(t),
                "exit_date": exit_date,
                "capital_at_risk": round(risk, 2) if risk is not None else None,
                "net_premium": round(_net_premium(t), 2) if _net_premium(t) is not None else None,
                "realized_pnl": round(realized, 2) if realized is not None else None,
                "unrealized_pnl": round(unrealized, 2) if unrealized is not None else None,
                "return_on_risk": round(ror_realized, 4)
                if ror_realized is not None
                else (round(ror_unreal, 4) if ror_unreal is not None else None),
                "pnl_kind": "realized"
                if realized is not None
                else ("unrealized" if unrealized is not None else ""),
                "legs_open": live.get("legs", 0),
            }
        )
    return rows


# ============================================================
# Outputs
# ============================================================
def write_ledger_csv(rows: list[dict], path: str) -> None:
    cols = [
        "thesis_id",
        "ticker",
        "type",
        "status",
        "entry_date",
        "exit_date",
        "capital_at_risk",
        "net_premium",
        "realized_pnl",
        "unrealized_pnl",
        "return_on_risk",
        "pnl_kind",
        "legs_open",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_returns_csv(rows: list[dict], path: str) -> int:
    """CLOSED trades → date,returns (return_on_risk). Same-day trades aggregated.

    Returns the number of dated return rows written.
    """
    by_date: dict = {}
    for r in rows:
        if r["status"] != "CLOSED" or r["realized_pnl"] is None or not r["exit_date"]:
            continue
        risk = r["capital_at_risk"] or 0
        if risk <= 0:
            continue
        d = by_date.setdefault(r["exit_date"], {"pnl": 0.0, "risk": 0.0})
        d["pnl"] += r["realized_pnl"]
        d["risk"] += risk
    dated = sorted(by_date.items())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "returns"])
        for day, agg in dated:
            w.writerow([day, round(agg["pnl"] / agg["risk"], 6)])
    return len(dated)


def append_run_log(msg: str) -> None:
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(RUN_LOG, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ============================================================
# Summary
# ============================================================
def _money(x) -> str:
    if x is None:
        return "  n/a"
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def print_summary(rows: list[dict], n_returns: int) -> None:
    deployed = [r for r in rows if r["status"] in OPEN_STATUSES]
    closed = [r for r in rows if r["status"] == "CLOSED" and r["realized_pnl"] is not None]
    dead = [r for r in rows if r["status"] in DEAD_STATUSES]

    open_risk = sum((r["capital_at_risk"] or 0) for r in deployed)
    unreal = sum((r["unrealized_pnl"] or 0) for r in deployed if r["unrealized_pnl"] is not None)
    has_live = any(r["unrealized_pnl"] is not None for r in deployed)
    realized_total = sum((r["realized_pnl"] or 0) for r in closed)
    wins = [r for r in closed if (r["realized_pnl"] or 0) > 0]

    print("\n" + "=" * 64)
    print("  TRADE LEDGER — strategy-level (return on deployed capital)")
    print("=" * 64)
    print(f"  Theses total        : {len(rows)}")
    print(f"  Open positions      : {len(deployed)}   (capital at risk {_money(open_risk)})")
    if has_live:
        ror = (unreal / open_risk) if open_risk else 0
        print(f"  Open unrealized P&L : {_money(unreal)}   ({ror * 100:+.1f}% of risk)")
        for r in deployed:
            if r["unrealized_pnl"] is not None:
                print(
                    f"      {r['ticker']:6s} {_money(r['unrealized_pnl']):>9s}  "
                    f"({r['legs_open']} legs, risk {_money(r['capital_at_risk'])})"
                )
    else:
        print("  Open unrealized P&L : (no live option legs found in Alpaca)")
    print(f"  Closed trades       : {len(closed)}")
    if closed:
        wr = len(wins) / len(closed) * 100
        avg_r = sum((r["return_on_risk"] or 0) for r in closed) / len(closed)
        print(f"  Realized P&L        : {_money(realized_total)}")
        print(f"  Win rate            : {wr:.0f}%   avg return on risk {avg_r * 100:+.1f}%")
    print(f"  Never deployed      : {len(dead)} (ideas/expired)")
    print("-" * 64)
    if n_returns < 2:
        print(f"  Returns series: {n_returns} dated point(s). Need >=2 CLOSED trades")
        print("  before a tearsheet is meaningful. Ledger is still written.")
    else:
        print(f"  Returns series: {n_returns} dated points → tearsheet-ready (--report).")
    print("=" * 64)


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="Trade-level ledger + returns from theses.")
    ap.add_argument(
        "--report", action="store_true", help="render a quantstats tearsheet if >=2 closed trades"
    )
    ap.add_argument(
        "--no-positions",
        action="store_true",
        help="skip Alpaca live positions (offline ledger only)",
    )
    ap.add_argument("--open", action="store_true", help="open the tearsheet in a browser")
    args = ap.parse_args()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    day = dt.datetime.now().strftime("%Y-%m-%d")

    theses = load_theses()
    if not theses:
        print("No theses found in", THESES_DIR)
        return 1

    opl = {} if args.no_positions else option_pnl_by_underlying(alpaca_positions())
    rows = build_ledger(theses, opl)

    ledger_csv = f"{REPORTS_DIR}/trade_ledger_{stamp}.csv"
    returns_csv = f"{REPORTS_DIR}/trade_returns_{stamp}.csv"
    write_ledger_csv(rows, ledger_csv)
    n_returns = write_returns_csv(rows, returns_csv)

    print_summary(rows, n_returns)
    print(f"\n  Ledger : {ledger_csv}")
    print(f"  Returns: {returns_csv}")
    append_run_log(
        f"{dt.datetime.now().isoformat(timespec='seconds')}  trade_ledger  "
        f"{len(rows)} theses, {n_returns} closed-return points  -> {ledger_csv}"
    )

    if args.report:
        if n_returns >= 2:
            cmd = [
                sys.executable,
                f"{BASE_DIR}/scripts/perf_report.py",
                "--csv",
                returns_csv,
                "--title",
                f"Campaign trades (R) {day}",
                "--no-benchmark",
            ]
            cmd.append("--open" if args.open else "--no-open")
            print("\n  Chaining → perf_report.py on the trade returns...")
            subprocess.run(cmd, check=False)
        else:
            print("\n  --report skipped: need >=2 closed trades for a tearsheet.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
