#!/usr/bin/env python3
"""
FMP API Client for Mover Screener

Provides rate-limited access to Financial Modeling Prep API for the
high-beta secondary-mover screen.

IMPORTANT: Uses ONLY /stable/ endpoints. FMP retired every /api/v3/ endpoint
on Aug 31 2025 (they now 403 "Legacy Endpoint" on non-grandfathered plans),
so this client never touches v3.

The candidate universe comes from finvizfinance (see universe_source.py); FMP
is used here only for the authoritative per-name technical pass.

Endpoint notes (verified live):
- /stable/profile            : authoritative beta + 52w range per symbol (list).
- /stable/historical-price-eod/full : flat list, most-recent-first OHLCV.
- /stable/quote              : ONE symbol per call. Comma-batching silently
                               returns [] on this plan, so we loop per symbol.

Features:
- Rate limiting (0.25s between requests)
- Automatic retry on 429 errors
- Session caching for duplicate requests
- Graceful degradation (returns None / [] instead of raising)
"""

import os
import sys
import time
from datetime import date, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


class FMPClient:
    """Client for Financial Modeling Prep /stable/ API with rate limiting."""

    STABLE_URL = "https://financialmodelingprep.com/stable"
    RATE_LIMIT_DELAY = 0.25  # 250ms between requests

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Set FMP_API_KEY environment variable "
                "or pass api_key parameter."
            )
        self.session = requests.Session()
        self.cache: dict = {}
        self.last_call_time = 0.0
        self.rate_limit_reached = False
        self.retry_count = 0
        self.max_retries = 1
        self.api_calls_made = 0
        # Symbols not available on the current plan (FMP 402/403). On a market-wide
        # scan some tickers (foreign/index/special) are simply off-plan -- that's a
        # quiet, expected skip, not an error. Tracked so we can report a count.
        self.plan_skipped: set = set()

    def _rate_limited_get(
        self, url: str, params: Optional[dict] = None, quiet: bool = False
    ) -> Optional[object]:
        if self.rate_limit_reached:
            return None

        params = dict(params) if params else {}
        params["apikey"] = self.api_key

        elapsed = time.time() - self.last_call_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            response = self.session.get(url, params=params, timeout=30)
            self.last_call_time = time.time()
            self.api_calls_made += 1

            if response.status_code == 200:
                self.retry_count = 0
                return response.json()
            elif response.status_code == 429:
                self.retry_count += 1
                if self.retry_count <= self.max_retries:
                    print("WARNING: Rate limit exceeded. Waiting 60 seconds...", file=sys.stderr)
                    time.sleep(60)
                    return self._rate_limited_get(url, params, quiet=quiet)
                print("ERROR: Daily API rate limit reached.", file=sys.stderr)
                self.rate_limit_reached = True
                return None
            elif response.status_code in (402, 403):
                # Symbol/endpoint not available on this plan. Expected during a
                # broad scan -- skip quietly, just record the symbol for a summary.
                sym = (params or {}).get("symbol")
                if sym:
                    self.plan_skipped.add(sym)
                return None
            else:
                if not quiet:
                    print(
                        f"ERROR: FMP request failed: {response.status_code} - "
                        f"{response.text[:200]}",
                        file=sys.stderr,
                    )
                return None
        except requests.exceptions.RequestException as e:
            print(f"ERROR: FMP request exception: {e}", file=sys.stderr)
            return None

    def get_profile(self, symbol: str) -> Optional[dict]:
        """Fetch /stable/profile (authoritative beta, 52w range). Returns one dict."""
        cache_key = f"profile_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._rate_limited_get(f"{self.STABLE_URL}/profile", {"symbol": symbol})
        if isinstance(data, list) and data:
            self.cache[cache_key] = data[0]
            return data[0]
        return None

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Fetch /stable/quote for a SINGLE symbol (comma-batching is unsupported)."""
        cache_key = f"quote_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._rate_limited_get(f"{self.STABLE_URL}/quote", {"symbol": symbol})
        if isinstance(data, list) and data:
            self.cache[cache_key] = data[0]
            return data[0]
        return None

    def get_historical(self, symbol: str, days: int = 260) -> list[dict]:
        """Fetch /stable/historical-price-eod/full, most-recent-first.

        The stable EOD endpoint ignores `timeseries`, so we bound the payload
        with a from/to range (2x calendar days covers N trading days) and then
        truncate to `days` rows.
        """
        cache_key = f"hist_{symbol}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        today = date.today()
        params = {
            "symbol": symbol,
            "from": (today - timedelta(days=days * 2)).isoformat(),
            "to": today.isoformat(),
        }
        data = self._rate_limited_get(f"{self.STABLE_URL}/historical-price-eod/full", params)
        if isinstance(data, list) and data:
            trimmed = data[:days]
            self.cache[cache_key] = trimmed
            return trimmed
        return []

    def get_cash_flow(self, symbol: str, period: str = "quarter", limit: int = 8) -> list[dict]:
        """Fetch /stable/cash-flow-statement (capex + free cash flow).

        Returns most-recent-first. `capitalExpenditure` is negative (outflow).
        Used as the buildout's leading indicator (hyperscaler capex trend).
        Degrades to [] on a paywalled/off-plan symbol.
        """
        cache_key = f"cashflow_{symbol}_{period}_{limit}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._rate_limited_get(
            f"{self.STABLE_URL}/cash-flow-statement",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        if isinstance(data, list) and data:
            self.cache[cache_key] = data
            return data
        return []

    def get_price_target_summary(self, symbol: str) -> Optional[dict]:
        """Fetch /stable/price-target-summary (analyst consensus targets).

        Returns one dict with lastMonth/lastQuarter avg price targets and counts.
        Used to flag names trading ABOVE consensus target ("extended").
        """
        cache_key = f"pts_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._rate_limited_get(f"{self.STABLE_URL}/price-target-summary", {"symbol": symbol})
        if isinstance(data, list) and data:
            self.cache[cache_key] = data[0]
            return data[0]
        return None

    def get_grades_consensus(self, symbol: str) -> Optional[dict]:
        """Fetch /stable/grades-consensus (analyst buy/hold/sell tallies).

        Returns one dict: strongBuy, buy, hold, sell, strongSell, consensus.
        """
        cache_key = f"grades_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._rate_limited_get(f"{self.STABLE_URL}/grades-consensus", {"symbol": symbol})
        if isinstance(data, list) and data:
            self.cache[cache_key] = data[0]
            return data[0]
        return None

    def get_api_stats(self) -> dict:
        return {
            "cache_entries": len(self.cache),
            "api_calls_made": self.api_calls_made,
            "rate_limit_reached": self.rate_limit_reached,
            "plan_skipped_count": len(self.plan_skipped),
            "plan_skipped": sorted(self.plan_skipped),
        }
