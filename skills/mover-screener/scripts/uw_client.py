#!/usr/bin/env python3
"""
Unusual Whales API Client for Mover Screener

Provides the squeeze + IV overlays that tell us which high-beta names are
primed for the most violent swings.

Endpoints (verified live, base https://api.unusualwhales.com/api):
- shorts/{ticker}/data            : {"data": [...]} borrow fee_rate (str),
                                     short_shares_available, rebate_rate.
                                     Most-recent-first; take [0].
- shorts/{ticker}/interest-float  : {"data": [...]} short interest % of float.
                                     OFTEN EMPTY ({"data": []}) for newer names
                                     like APLD -- must degrade gracefully.
- stock/{ticker}/iv-rank          : {"data": [...]} iv_rank_1y, volatility, close.
                                     Ascending dates; take the LAST entry.

Auth: Bearer token (UNUSUAL_WHALES_API_KEY). All getters return None / empty
on any failure so the screener never crashes on a missing overlay.
"""

import os
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def _to_float(value) -> Optional[float]:
    """UW returns numbers as strings ('0.3475'). Coerce, tolerate None/''."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class UnusualWhalesClient:
    """Client for Unusual Whales squeeze + IV overlays."""

    BASE_URL = "https://api.unusualwhales.com/api"
    RATE_LIMIT_DELAY = 0.3

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("UNUSUAL_WHALES_API_KEY")
        self.available = bool(self.api_key)
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        self.cache: dict = {}
        self.last_call_time = 0.0
        self.api_calls_made = 0

    def _get(self, path: str) -> Optional[object]:
        if not self.available:
            return None
        if path in self.cache:
            return self.cache[path]

        elapsed = time.time() - self.last_call_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            response = self.session.get(f"{self.BASE_URL}/{path}", timeout=30)
            self.last_call_time = time.time()
            self.api_calls_made += 1
            if response.status_code == 200:
                data = response.json()
                self.cache[path] = data
                return data
            if response.status_code in (401, 403):
                print(
                    f"WARNING: Unusual Whales auth failed ({response.status_code}) - "
                    "squeeze/IV overlays disabled.",
                    file=sys.stderr,
                )
                self.available = False
            return None
        except requests.exceptions.RequestException as e:
            print(f"WARNING: UW request exception for {path}: {e}", file=sys.stderr)
            return None

    def get_shorts_data(self, symbol: str) -> dict:
        """Latest borrow data. Returns {} when unavailable."""
        data = self._get(f"shorts/{symbol}/data")
        rows = data.get("data") if isinstance(data, dict) else None
        if not rows:
            return {}
        latest = rows[0]
        return {
            "fee_rate": _to_float(latest.get("fee_rate")),
            "short_shares_available": _to_float(latest.get("short_shares_available")),
            "rebate_rate": _to_float(latest.get("rebate_rate")),
            "timestamp": latest.get("timestamp"),
        }

    def get_short_interest_float(self, symbol: str) -> Optional[float]:
        """Short interest as % of float. Returns None when UW has no data
        (the {"data": []} case is common for newer names like APLD)."""
        data = self._get(f"shorts/{symbol}/interest-float")
        rows = data.get("data") if isinstance(data, dict) else None
        if not rows:
            return None
        latest = rows[-1] if isinstance(rows, list) else rows
        # Field name varies; probe the common keys.
        for key in ("short_interest_float", "short_percent_of_float", "percent_of_float"):
            val = _to_float(latest.get(key)) if isinstance(latest, dict) else None
            if val is not None:
                # UW sometimes returns a fraction (0.12) vs a percent (12.0).
                return val * 100 if val <= 1.0 else val
        return None

    def get_iv_rank(self, symbol: str) -> Optional[float]:
        """Current 1y IV rank (0-100). Data is ascending, so take the last row."""
        data = self._get(f"stock/{symbol}/iv-rank")
        rows = data.get("data") if isinstance(data, dict) else None
        if not rows:
            return None
        latest = rows[-1]
        return _to_float(latest.get("iv_rank_1y"))

    # ------------------------------------------------------------------
    # Smart-money pre-positioning (market-wide options flow + dealer gamma)
    # ------------------------------------------------------------------

    def get_market_flow_alerts(self, pages: int = 6, per_page: int = 200) -> list[dict]:
        """Pull the market-wide unusual options flow-alerts feed (newest first),
        paginating via the `older_than` cursor. Each alert is one unusual
        sweep/block with ticker, type, premium, sweep flag, opening-trade flag,
        volume/OI ratio, ask/bid-side premium, next_earnings_date, sector.

        This is the DISCOVERY source: we find tickers from the flow, rather than
        checking a known ticker. Returns the raw alert list.
        """
        if not self.available:
            return []
        alerts: list[dict] = []
        cursor = None
        for _ in range(max(1, pages)):
            path = f"option-trades/flow-alerts?limit={per_page}"
            if cursor:
                path += f"&older_than={cursor}"
            # bypass the path cache for paginated cursors
            elapsed = time.time() - self.last_call_time
            if elapsed < self.RATE_LIMIT_DELAY:
                time.sleep(self.RATE_LIMIT_DELAY - elapsed)
            try:
                resp = self.session.get(f"{self.BASE_URL}/{path}", timeout=30)
                self.last_call_time = time.time()
                self.api_calls_made += 1
                if resp.status_code != 200:
                    break
                payload = resp.json()
            except requests.exceptions.RequestException as e:
                print(f"WARNING: UW flow-alerts page failed: {e}", file=sys.stderr)
                break
            batch = payload.get("data", []) if isinstance(payload, dict) else []
            if not batch:
                break
            alerts.extend(batch)
            cursor = payload.get("older_than")
            if not cursor:
                break
        return alerts

    def gamma_building(self, symbol: str, days: int = 5) -> dict:
        """Dealer gamma trend from greek-exposure (most-recent `days`).

        Net gamma = call_gamma + put_gamma per day. A rising net gamma magnitude
        means dealers are accumulating exposure -> price often pins/compresses,
        then moves hard when it unwinds. Returns {net_gamma, trend, building}.
        """
        data = self._get(f"stock/{symbol}/greek-exposure?limit={max(2, days)}")
        rows = data.get("data") if isinstance(data, dict) else None
        if not rows or len(rows) < 2:
            return {"net_gamma": None, "trend": None, "building": False}
        series = []
        for r in rows[:days]:
            cg = _to_float(r.get("call_gamma")) or 0.0
            pg = _to_float(r.get("put_gamma")) or 0.0
            series.append(cg + pg)
        # rows order may be newest-first or oldest-first; greek-exposure is
        # newest-first in practice. Compare latest vs oldest in the window.
        latest, oldest = series[0], series[-1]
        net = latest
        rising = abs(latest) > abs(oldest) * 1.10
        return {
            "net_gamma": round(net, 0),
            "trend": "building" if rising else "flat",
            "building": rising,
        }

    def squeeze_overlay(self, symbol: str) -> dict:
        """Combine borrow + SI%float into a squeeze read.

        Squeeze pressure rises with: high borrow fee_rate, low
        short_shares_available, and high short interest % of float. These are
        the names that throw the most violent swings on a catalyst.
        """
        shorts = self.get_shorts_data(symbol)
        si_float = self.get_short_interest_float(symbol)

        fee_rate = shorts.get("fee_rate")
        shares_avail = shorts.get("short_shares_available")

        flags = []
        # fee_rate is an annualized borrow cost already in PERCENT units
        # (verified live: fee_rate + rebate_rate ~= the broker base rate, so a
        # general-collateral name like APLD reads ~0.35 = 0.35%). Easy-to-borrow
        # names sit near ~0.3-0.5%; 5%+ means real borrow pressure, 20%+ is hot.
        if fee_rate is not None and fee_rate >= 5.0:
            flags.append(f"hard-to-borrow ({fee_rate:.1f}% fee)")
        if shares_avail is not None and 0 < shares_avail <= 500_000:
            flags.append(f"thin borrow ({shares_avail / 1e6:.2f}M avail)")
        if si_float is not None and si_float >= 15:
            flags.append(f"high SI {si_float:.0f}% float")

        if not flags:
            level = "low"
        elif len(flags) == 1:
            level = "moderate"
        else:
            level = "high"

        return {
            "fee_rate": fee_rate,
            "short_shares_available": shares_avail,
            "short_interest_float_pct": si_float,
            "squeeze_level": level,
            "squeeze_flags": flags,
        }
