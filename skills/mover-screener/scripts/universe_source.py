#!/usr/bin/env python3
"""
Universe Source - candidate discovery for the mover screener.

Two interchangeable backends behind one abstraction:

  finviz_free  (DEFAULT)  -- finvizfinance public Technical screener. Free,
                            ~15-min delayed, paginated, ~unlimited rows in
                            practice for our filters. No key required.

  finviz_elite (OPTIONAL) -- FINVIZ Elite CSV export (real-time, no row cap,
                            extra columns like float). Activates AUTOMATICALLY
                            when FINVIZ_API_KEY is set; otherwise we silently
                            fall back to finviz_free. Stubbed-but-wired: the
                            request path is implemented and parses the Elite
                            CSV, so enabling it is purely "set the env var".

Both backends return a list of candidate dicts with the SAME shape:

    {
        "symbol": "APLD",
        "beta": 5.7,            # may be None on free if FINVIZ omits it
        "atr": 3.62,            # dollar ATR from FINVIZ (proxy; FMP recomputes)
        "atr_pct": 8.1,         # atr / price * 100 (proxy)
        "price": 45.87,
        "pct_from_52w_high": -6.4,   # negative = below high
        "sma20_rel": -16.8,     # % distance of price vs SMA (FINVIZ relative)
        "sma50_rel": -4.5,
        "sma200_rel": 26.4,
        "volume": 21723239,
        "source": "finviz_free",
    }

The per-name FMP technical pass recomputes ATR%/DMAs/returns precisely; these
FINVIZ values are a fast prefilter and a fallback if FMP fails for a name.
"""

import csv
import io
import os
import sys
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:
    from finvizfinance.screener.technical import Technical

    HAS_FINVIZFINANCE = True
except ImportError:
    Technical = None  # type: ignore[assignment,misc]
    HAS_FINVIZFINANCE = False


# Map a requested minimum beta to the nearest FINVIZ "Over X" beta filter.
# FINVIZ only offers discrete thresholds, so we pick the largest one that does
# not exceed the requested floor (so we never miss qualifying names).
_FINVIZ_BETA_STEPS = [
    (3.0, "Over 3"),
    (2.5, "Over 2.5"),
    (2.0, "Over 2"),
    (1.5, "Over 1.5"),
    (1.0, "Over 1"),
    (0.5, "Over 0.5"),
]

# Map a requested minimum average volume (shares) to FINVIZ's discrete filter.
_FINVIZ_AVGVOL_STEPS = [
    (2_000_000, "Over 2M"),
    (1_000_000, "Over 1M"),
    (750_000, "Over 750K"),
    (500_000, "Over 500K"),
    (300_000, "Over 300K"),
    (200_000, "Over 200K"),
    (100_000, "Over 100K"),
]


def _beta_filter(min_beta: float) -> str:
    for floor, label in _FINVIZ_BETA_STEPS:
        if min_beta >= floor:
            return label
    return "Over 0.5"


def _avgvol_filter(min_avg_vol: int) -> str:
    for floor, label in _FINVIZ_AVGVOL_STEPS:
        if min_avg_vol >= floor:
            return label
    return "Over 100K"


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("%", "").replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(val) -> Optional[int]:
    f = _to_float(val)
    return int(f) if f is not None else None


def _rel_to_pct(val) -> Optional[float]:
    """FINVIZ relative SMA / 52W-High columns come as decimals (-0.1683 = -16.83%)."""
    f = _to_float(val)
    if f is None:
        return None
    # finvizfinance returns 0-1 range decimals; FINVIZ Elite CSV gives "%" strings
    # already handled by _to_float stripping '%'. Detect decimal form by magnitude.
    if abs(f) <= 1.5:
        return round(f * 100.0, 2)
    return round(f, 2)


# ---------------------------------------------------------------------------
# finviz_free backend
# ---------------------------------------------------------------------------


def _fetch_finviz_free(
    min_beta: float, min_avg_vol: int, min_price: float, max_rows: int
) -> list[dict]:
    if not HAS_FINVIZFINANCE:
        print(
            "WARNING: finvizfinance not installed. Install with: pip install finvizfinance",
            file=sys.stderr,
        )
        return []

    # NB: the Technical view has no "Type"/"stocks only" filter (that lives on the
    # Overview view). ETFs almost never clear beta>=1.5 + these vol/price floors,
    # and the per-name FMP profile pass filters anything non-equity downstream.
    # The Small-cap+ ($300M) floor drops micro-cap junk whose FINVIZ beta is
    # statistical noise (e.g. beta 16 names) and whose options are untradeable.
    filters_dict = {
        "Beta": _beta_filter(min_beta),
        "Average Volume": _avgvol_filter(min_avg_vol),
        "Price": "Over $10" if min_price <= 10 else "Over $20",
        "Market Cap.": "+Small (over $300mln)",
    }

    try:
        screener = Technical()
        screener.set_filter(filters_dict=filters_dict)
        # Order by Beta desc so the most volatile movers come first within the cap.
        df = screener.screener_view(order="Beta", limit=max_rows, verbose=0, ascend=False)
    except Exception as e:
        print(f"WARNING: finvizfinance screener failed: {e}", file=sys.stderr)
        return []

    if df is None or df.empty:
        return []

    rows: list[dict] = []
    for _, r in df.iterrows():
        symbol = str(r.get("Ticker", "")).strip()
        if not symbol:
            continue
        price = _to_float(r.get("Price"))
        atr = _to_float(r.get("ATR"))
        atr_pct = round(atr / price * 100, 2) if (atr and price) else None
        rows.append(
            {
                "symbol": symbol,
                "beta": _to_float(r.get("Beta")),
                "atr": atr,
                "atr_pct": atr_pct,
                "price": price,
                "pct_from_52w_high": _rel_to_pct(r.get("52W High")),
                "sma20_rel": _rel_to_pct(r.get("SMA20")),
                "sma50_rel": _rel_to_pct(r.get("SMA50")),
                "sma200_rel": _rel_to_pct(r.get("SMA200")),
                "volume": _to_int(r.get("Volume")),
                "source": "finviz_free",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# finviz_elite backend (stubbed-but-wired; activates when FINVIZ_API_KEY set)
# ---------------------------------------------------------------------------

_ELITE_EXPORT_URL = "https://elite.finviz.com/export.ashx"

# FINVIZ Elite URL filter codes for our profile screen.
_ELITE_BETA_CODES = {
    3.0: "fa_beta_o3",
    2.5: "fa_beta_o2.5",
    2.0: "fa_beta_o2",
    1.5: "fa_beta_o1.5",
    1.0: "fa_beta_o1",
}
_ELITE_AVGVOL_CODES = {
    2_000_000: "sh_avgvol_o2000",
    1_000_000: "sh_avgvol_o1000",
    500_000: "sh_avgvol_o500",
    100_000: "sh_avgvol_o100",
}


def _elite_beta_code(min_beta: float) -> str:
    for floor in sorted(_ELITE_BETA_CODES, reverse=True):
        if min_beta >= floor:
            return _ELITE_BETA_CODES[floor]
    return "fa_beta_o1"


def _elite_avgvol_code(min_avg_vol: int) -> str:
    for floor in sorted(_ELITE_AVGVOL_CODES, reverse=True):
        if min_avg_vol >= floor:
            return _ELITE_AVGVOL_CODES[floor]
    return "sh_avgvol_o100"


def _fetch_finviz_elite(
    api_key: str, min_beta: float, min_avg_vol: int, min_price: float, max_rows: int
) -> list[dict]:
    """Fetch via FINVIZ Elite CSV export (real-time, no row cap, float column).

    The Technical export view (v=171) returns Beta, ATR, SMA20/50/200 (relative),
    52W High (relative), Price and Volume columns, matching the free backend shape.
    """
    if requests is None:
        print("WARNING: requests not installed; cannot use FINVIZ Elite.", file=sys.stderr)
        return []

    filters = ",".join(
        [
            _elite_beta_code(min_beta),
            _elite_avgvol_code(min_avg_vol),
            "sh_price_o10" if min_price <= 10 else "sh_price_o20",
            "cap_smallover",  # Small-cap+ ($300M) floor; drops micro-cap junk
            "ind_stocksonly",
        ]
    )
    params = {
        "v": "171",  # technical export view
        "f": filters,
        "o": "-beta",  # order by beta desc
        "auth": api_key,
    }
    try:
        resp = requests.get(_ELITE_EXPORT_URL, params=params, timeout=30)
        if resp.status_code != 200:
            print(
                f"WARNING: FINVIZ Elite export HTTP {resp.status_code}; "
                "falling back to finviz_free.",
                file=sys.stderr,
            )
            return []
        reader = csv.DictReader(io.StringIO(resp.text))
        rows: list[dict] = []
        for r in reader:
            symbol = (r.get("Ticker") or "").strip()
            if not symbol:
                continue
            price = _to_float(r.get("Price"))
            atr = _to_float(r.get("ATR") or r.get("Average True Range"))
            atr_pct = round(atr / price * 100, 2) if (atr and price) else None
            rows.append(
                {
                    "symbol": symbol,
                    "beta": _to_float(r.get("Beta")),
                    "atr": atr,
                    "atr_pct": atr_pct,
                    "price": price,
                    "pct_from_52w_high": _rel_to_pct(r.get("52-Week High") or r.get("52W High")),
                    "sma20_rel": _rel_to_pct(
                        r.get("20-Day Simple Moving Average") or r.get("SMA20")
                    ),
                    "sma50_rel": _rel_to_pct(
                        r.get("50-Day Simple Moving Average") or r.get("SMA50")
                    ),
                    "sma200_rel": _rel_to_pct(
                        r.get("200-Day Simple Moving Average") or r.get("SMA200")
                    ),
                    "volume": _to_int(r.get("Volume")),
                    "float_shares": _to_int(r.get("Shares Float") or r.get("Float")),
                    "source": "finviz_elite",
                }
            )
            if len(rows) >= max_rows:
                break
        return rows
    except Exception as e:
        print(f"WARNING: FINVIZ Elite export failed: {e}; falling back.", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_universe(
    source: str = "finviz_free",
    min_beta: float = 1.5,
    min_avg_vol: int = 1_000_000,
    min_price: float = 10.0,
    max_rows: int = 400,
    universe_file: Optional[str] = None,
) -> tuple[list[dict], str]:
    """Return (candidates, backend_used).

    `source` may be "finviz_free" or "finviz_elite". Elite is only used when
    FINVIZ_API_KEY is present; otherwise we transparently fall back to free.
    Hand-curated names from `universe_file` are merged in (deduped by symbol),
    so a name in the file is always screened even if it falls outside the
    FINVIZ prefilter.
    """
    elite_key = os.getenv("FINVIZ_API_KEY")
    backend = "finviz_free"
    rows: list[dict] = []

    if source == "finviz_elite" and elite_key:
        rows = _fetch_finviz_elite(elite_key, min_beta, min_avg_vol, min_price, max_rows)
        if rows:
            backend = "finviz_elite"
        else:
            # Elite path failed/empty -> graceful fallback.
            rows = _fetch_finviz_free(min_beta, min_avg_vol, min_price, max_rows)
            backend = "finviz_free (elite fallback)"
    else:
        if source == "finviz_elite" and not elite_key:
            print(
                "NOTE: --universe-source finviz_elite requested but FINVIZ_API_KEY "
                "is not set; using free backend.",
                file=sys.stderr,
            )
        rows = _fetch_finviz_free(min_beta, min_avg_vol, min_price, max_rows)
        backend = "finviz_free"

    # Merge hand-curated names (UNIVERSE.md Tier-2 list, etc.)
    if universe_file:
        seen = {r["symbol"].upper() for r in rows}
        for sym in _read_universe_file(universe_file):
            if sym.upper() not in seen:
                rows.append({"symbol": sym, "source": "universe_file"})
                seen.add(sym.upper())

    return rows, backend


# Common English / finance words that look like tickers in prose. Without this,
# a markdown file like UNIVERSE.md leaks "AI", "FILL", "STOPS", "ENTRY" etc. as
# fake tickers -> wasted FMP calls + 402s.
_PROSE_STOPWORDS = {
    "A",
    "AI",
    "AND",
    "ARE",
    "ALL",
    "ANY",
    "BE",
    "BOTH",
    "BUT",
    "CAP",
    "CALL",
    "CALLS",
    "DO",
    "EACH",
    "ENTRY",
    "ETF",
    "ETFS",
    "EV",
    "FAST",
    "FILL",
    "FIRST",
    "FOR",
    "GATE",
    "HPC",
    "IN",
    "IS",
    "IT",
    "IV",
    "IVR",
    "LAST",
    "LESS",
    "MORE",
    "MOVES",
    "NAMES",
    "NEWS",
    "NOT",
    "OI",
    "ON",
    "OR",
    "PAY",
    "PUT",
    "PUTS",
    "RISK",
    "SET",
    "STOP",
    "STOPS",
    "THE",
    "THAT",
    "THIS",
    "THOSE",
    "THROW",
    "TO",
    "TOO",
    "US",
    "USD",
    "USER",
    "VOL",
    "WHEN",
    "WIDE",
    "WITH",
    "BETA",
    "TIER",
    "DMA",
    "ATR",
    "DTE",
    "GEX",
    "PE",
    "FCF",
    "YOY",
    "QOQ",
    "EPS",
    "DRAG",
    "GIVE",
    "JUST",
    "HUNT",
    "SECTOR",
    "THEME",
    "STYLE",
}


# Pure metric/markup noise that is NEVER a ticker (safe to drop even in bucket
# mode). Excludes real-ticker/word collisions like BE, ON, ALL, IT, PR, US, DO.
_METRIC_NOISE = {
    "BETA",
    "IVR",
    "ATR",
    "DTE",
    "GEX",
    "YOY",
    "QOQ",
    "EPS",
    "HPC",
    "DMA",
    "TIER",
    "ETF",
    "ETFS",
    "FCF",
    "OI",
    "IV",
    "VOL",
}


def _clean_ticker_token(tok: str, stopwords: bool = False) -> Optional[str]:
    """Normalize one token to a plausible ticker or None.

    Strips $ prefix and surrounding punctuation (so 'stops.' -> 'STOPS',
    'NVDA,' -> 'NVDA') and uppercases. Always drops pure metric noise (BETA/IVR);
    when `stopwords=True` (plain-list mode) also drops common English prose words.
    """
    tok = tok.strip().lstrip("$").strip(".,;:()[]*•-").upper()
    if not tok:
        return None
    core = tok.replace(".", "").replace("-", "")
    if not (1 <= len(core) <= 5 and core.isalpha()):
        return None
    if core in _METRIC_NOISE:
        return None
    if stopwords and (tok in _PROSE_STOPWORDS or core in _PROSE_STOPWORDS):
        return None
    return tok


def _read_universe_file(path: str) -> list[str]:
    """Read tickers from a file. Handles BOTH a plain ticker list (one-per-line or
    comma/space separated) AND a prose-markdown file like UNIVERSE.md.

    If the file has 'Tier ... :' bucket lines (UNIVERSE.md), only those lines are
    mined (text after the colon, parentheticals stripped) -- this avoids scraping
    English prose ('AI', 'FILL', 'STOPS') as fake tickers. Otherwise every line's
    tokens are read. A stopword filter guards both paths.
    """
    import re

    raw_lines: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            raw_lines = fh.readlines()
    except OSError as e:
        print(f"WARNING: could not read --universe-file {path}: {e}", file=sys.stderr)
        return []

    has_tier_buckets = any("tier" in ln.partition(":")[0].lower() and ":" in ln for ln in raw_lines)

    syms: list[str] = []
    for line in raw_lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if has_tier_buckets:
            head, sep, after = line.partition(":")
            if not sep or "tier" not in head.lower():
                continue
            after = re.sub(r"\([^)]*\)", " ", after)  # drop "(beta 5.7)" notes
            apply_stopwords = False  # tier lines are pure ticker lists already
        else:
            after = line
            apply_stopwords = True  # plain text -> guard against prose words
        for tok in after.replace(",", " ").split():
            t = _clean_ticker_token(tok, stopwords=apply_stopwords)
            if t:
                syms.append(t)

    out, seen = [], set()
    for s in syms:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out
