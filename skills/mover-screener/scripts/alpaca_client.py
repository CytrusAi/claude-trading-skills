#!/usr/bin/env python3
"""
Alpaca Options Client for Mover Screener -- the LIQUIDITY GATE.

Secondary movers often have thin option chains. A great technical setup with
untradeable options is a non-starter (we got burned by AMZN -- a mega-cap --
whose options wouldn't fill at a usable spread). This module checks the chain
before any name reaches the shortlist.

Gate rules (per UNIVERSE.md):
- Use the nearest STANDARD MONTHLY expiration (3rd Friday) ~30-60 DTE.
  Weeklies are illiquid for secondary names -- monthlies only.
- Evaluate the near-the-money (ATM) strike, not the whole chain (far OTM/ITM
  strikes are wide even on liquid names).
- Require a live two-sided quote (bid > 0 AND ask > 0).
- Require bid/ask spread <= max_spread_pct of mid (default 10%).
- Fail -> "ILLIQUID -- spread only or skip".

Endpoint (verified live):
  GET https://data.alpaca.markets/v1beta1/options/snapshots/{underlying}
      ?feed=indicative&type=call&expiration_date=YYYY-MM-DD
      &strike_price_gte=..&strike_price_lte=..
  Auth headers: APCA-API-KEY-ID / APCA-API-SECRET-KEY
  latestQuote fields: bp (bid), ap (ask), bs/as (sizes).

feed=indicative is used because OPRA requires a paid market-data add-on; it
still returns two-sided quotes whose spreads cleanly separate liquid (SPY ATM
~2-7%) from thin (GDS ATM ~35-50%) chains.
"""

import os
import sys
import time
from datetime import date
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def next_monthly_expiration(
    min_dte: int = 30, max_dte: int = 60, today: Optional[date] = None
) -> date:
    """Return the standard monthly expiration (3rd Friday) whose DTE best fits
    the [min_dte, max_dte] window, picking the one closest to the window mid.

    If none fall inside the window (calendar gaps), return the 3rd Friday with
    DTE closest to the window midpoint anyway.
    """
    today = today or date.today()
    target = (min_dte + max_dte) / 2
    candidates = []
    for offset in range(0, 4):
        month = today.month + offset
        year = today.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        third_friday = _third_friday(year, month)
        dte = (third_friday - today).days
        if dte <= 0:
            continue
        candidates.append((third_friday, dte))

    in_window = [c for c in candidates if min_dte <= c[1] <= max_dte]
    pool = in_window or candidates
    pool.sort(key=lambda c: abs(c[1] - target))
    return pool[0][0]


def _third_friday(year: int, month: int) -> date:
    """Date of the 3rd Friday of a month (standard monthly option expiry)."""
    d = date(year, month, 1)
    # weekday(): Mon=0 .. Fri=4
    first_friday = 1 + (4 - d.weekday()) % 7
    return date(year, month, first_friday + 14)


class AlpacaOptionsClient:
    """Alpaca options-data client implementing the monthly liquidity gate."""

    DATA_URL = "https://data.alpaca.markets/v1beta1/options"
    RATE_LIMIT_DELAY = 0.2

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        feed: str = "indicative",
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET")
        self.feed = feed
        self.available = bool(self.api_key and self.api_secret)
        self.session = requests.Session()
        if self.available:
            self.session.headers.update(
                {
                    "APCA-API-KEY-ID": self.api_key,
                    "APCA-API-SECRET-KEY": self.api_secret,
                }
            )
        self.cache: dict = {}
        self.last_call_time = 0.0
        self.api_calls_made = 0

    def _get(self, path: str, params: dict) -> Optional[dict]:
        if not self.available:
            return None

        elapsed = time.time() - self.last_call_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            response = self.session.get(f"{self.DATA_URL}/{path}", params=params, timeout=30)
            self.last_call_time = time.time()
            self.api_calls_made += 1
            if response.status_code == 200:
                return response.json()
            if response.status_code in (401, 403):
                print(
                    f"WARNING: Alpaca auth failed ({response.status_code}) - "
                    "liquidity gate disabled.",
                    file=sys.stderr,
                )
                self.available = False
            return None
        except requests.exceptions.RequestException as e:
            print(f"WARNING: Alpaca request exception: {e}", file=sys.stderr)
            return None

    def liquidity_gate(
        self,
        underlying: str,
        spot: float,
        option_type: str = "call",
        max_spread_pct: float = 10.0,
        min_dte: int = 30,
        max_dte: int = 60,
        strike_band_pct: float = 12.0,
    ) -> dict:
        """Evaluate the ATM contract on the nearest monthly expiration.

        option_type: "call" (Stage 2 / long bias) or "put" (Stage 4 / short
        bias) -- we price the side we would actually trade.

        Returns a dict with passed (bool|None), expiration, strike, bid, ask,
        spread_pct, dte, and a human reason. passed=None means "unknown"
        (gate unavailable / no data) -- the caller should surface that, not
        treat it as a hard fail.
        """
        opt_type = "put" if str(option_type).lower().startswith("p") else "call"
        result = {
            "passed": None,
            "option_type": opt_type,
            "expiration": None,
            "strike": None,
            "bid": None,
            "ask": None,
            "spread_pct": None,
            "dte": None,
            "reason": "gate unavailable",
        }
        if not self.available:
            return result
        if not spot or spot <= 0:
            result["reason"] = "no spot price"
            return result

        expiration = next_monthly_expiration(min_dte, max_dte)
        dte = (expiration - date.today()).days
        result["expiration"] = expiration.isoformat()
        result["dte"] = dte

        lo = round(spot * (1 - strike_band_pct / 100), 2)
        hi = round(spot * (1 + strike_band_pct / 100), 2)
        params = {
            "feed": self.feed,
            "type": opt_type,
            "expiration_date": expiration.isoformat(),
            "strike_price_gte": lo,
            "strike_price_lte": hi,
            "limit": 200,
        }
        data = self._get(f"snapshots/{underlying}", params)
        snapshots = data.get("snapshots", {}) if isinstance(data, dict) else {}
        if not snapshots:
            result["reason"] = "no contracts at monthly expiry (likely unoptionable)"
            result["passed"] = False
            return result

        atm = self._select_atm(snapshots, spot)
        if not atm:
            result["reason"] = "no near-the-money strike found"
            result["passed"] = False
            return result

        strike, bid, ask = atm
        result["strike"] = strike
        result["bid"] = bid
        result["ask"] = ask

        if bid <= 0 or ask <= 0:
            result["reason"] = "no live two-sided quote (one-sided / no bid)"
            result["passed"] = False
            return result

        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid * 100
        result["spread_pct"] = round(spread_pct, 1)

        if spread_pct <= max_spread_pct:
            result["passed"] = True
            result["reason"] = f"ATM spread {spread_pct:.1f}% <= {max_spread_pct:.0f}%"
        else:
            result["passed"] = False
            result["reason"] = (
                f"ILLIQUID -- ATM spread {spread_pct:.1f}% > {max_spread_pct:.0f}% "
                "(spread only or skip)"
            )
        return result

    @staticmethod
    def _select_atm(snapshots: dict, spot: float) -> Optional[tuple]:
        """Pick the strike closest to spot. Returns (strike, bid, ask)."""
        best = None
        best_dist = None
        for occ, snap in snapshots.items():
            strike = _strike_from_occ(occ)
            if strike is None:
                continue
            quote = snap.get("latestQuote", {}) if isinstance(snap, dict) else {}
            bid = quote.get("bp", 0) or 0
            ask = quote.get("ap", 0) or 0
            dist = abs(strike - spot)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = (strike, float(bid), float(ask))
        return best


def _strike_from_occ(occ: str) -> Optional[float]:
    """Parse strike from an OCC symbol, e.g. AAPL260717C00190000 -> 190.0.

    Layout: <root><yymmdd><C|P><strike*1000, 8 digits>.
    """
    if len(occ) < 9:
        return None
    digits = occ[-8:]
    if not digits.isdigit():
        return None
    return int(digits) / 1000.0
