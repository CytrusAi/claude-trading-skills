#!/usr/bin/env python3
"""
Technical indicators for the Mover Screener.

All functions take FMP /stable/historical-price-eod/full rows, which arrive
MOST-RECENT-FIRST with keys: date, open, high, low, close, volume.

The two swing proxies that define a "mover":
- beta   : how hard the name moves relative to the market (systematic swing).
- ATR%   : how wide the daily range is in percent terms (absolute swing).
High on both -> bigger directional option payoffs in either direction.
"""

from typing import Optional


def _closes(historical: list[dict]) -> list[float]:
    return [float(b["close"]) for b in historical if b.get("close") is not None]


def sma(historical: list[dict], period: int) -> Optional[float]:
    """Simple moving average of closes over `period` most-recent days."""
    closes = _closes(historical)
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period


def atr_percent(historical: list[dict], period: int = 14) -> Optional[float]:
    """Average True Range over `period` days, expressed as % of latest close.

    True Range = max(high-low, |high-prev_close|, |low-prev_close|). ATR% is the
    plain-English "how many percent does this thing swing in a day" number that
    drives option-stop width and premium.
    """
    if len(historical) < period + 1:
        return None
    trs = []
    for i in range(period):
        cur = historical[i]
        prev = historical[i + 1]
        high = float(cur["high"])
        low = float(cur["low"])
        prev_close = float(prev["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    atr = sum(trs) / period
    last_close = float(historical[0]["close"])
    if last_close <= 0:
        return None
    return atr / last_close * 100


def pct_return(historical: list[dict], days: int) -> Optional[float]:
    """Percent price return over the last `days` trading days."""
    closes = _closes(historical)
    if len(closes) <= days:
        return None
    now = closes[0]
    then = closes[days]
    if then <= 0:
        return None
    return (now - then) / then * 100


def fifty_two_week_high(historical: list[dict]) -> Optional[float]:
    """Highest high over the trailing ~252 trading days."""
    window = historical[:252]
    highs = [float(b["high"]) for b in window if b.get("high") is not None]
    return max(highs) if highs else None


def pct_from_high(historical: list[dict]) -> Optional[float]:
    """Distance from the 52w high as a signed percent (negative = below high)."""
    high = fifty_two_week_high(historical)
    if not high or high <= 0:
        return None
    last_close = float(historical[0]["close"])
    return (last_close - high) / high * 100


def realized_beta(
    stock_hist: list[dict], market_hist: list[dict], lookback: int = 180
) -> Optional[float]:
    """Realized beta of daily returns vs a market proxy (SPY) over `lookback`.

    Used as a FALLBACK when FMP's profile beta is null (recent listings like
    IREN/NBIS) or stale-low (it lags fast-moving names). We take
    max(profile_beta, realized_beta) downstream so genuinely swingy names are
    never excluded by a lagging vendor number.
    """
    sret = _daily_returns_by_date(stock_hist)
    mret = _daily_returns_by_date(market_hist)
    common = sorted(set(sret) & set(mret), reverse=True)[:lookback]
    if len(common) < 30:
        return None
    xs = [sret[d] for d in common]
    ms = [mret[d] for d in common]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_m = sum(ms) / n
    cov = sum((xs[i] - mean_x) * (ms[i] - mean_m) for i in range(n)) / n
    var_m = sum((m - mean_m) ** 2 for m in ms) / n
    if var_m <= 0:
        return None
    return cov / var_m


def _daily_returns_by_date(historical: list[dict]) -> dict:
    """Map date -> daily return. Rows are most-recent-first; return[t] uses t-1."""
    returns = {}
    for i in range(len(historical) - 1):
        cur = historical[i]
        prev = historical[i + 1]
        prev_close = float(prev["close"])
        if prev_close <= 0:
            continue
        returns[cur["date"]] = (float(cur["close"]) - prev_close) / prev_close
    return returns


def _atr_abs(historical: list[dict], period: int, offset: int = 0) -> Optional[float]:
    """Absolute ATR (price units) over `period` days starting at `offset`."""
    if len(historical) < offset + period + 1:
        return None
    trs = []
    for i in range(offset, offset + period):
        cur = historical[i]
        prev = historical[i + 1]
        high = float(cur["high"])
        low = float(cur["low"])
        prev_close = float(prev["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / period


def premove_signal(historical: list[dict]) -> dict:
    """Detect a 'coiled spring' -- volatility/range/volume contraction that often
    precedes a sharp move (Minervini VCP intuition, direction-neutral).

    Three contractions, each a ratio of RECENT vs BASELINE (lower = tighter):
      - atr_ratio   : ATR(10) / ATR(50)   -> daily-range volatility compression
      - range_ratio : last-10d (high-low)/close vs last-50d              -> coil
      - vol_ratio   : avg volume(10) / avg volume(50)  -> volume dry-up

    coiled = at least two of the three are contracting (ratio < threshold).
    premove_score 0-100: higher = tighter coil (closer to a release).
    Direction is decided elsewhere (Stage 2 coil -> CALL break; Stage 4 coil ->
    PUT break); this is the trigger, not the direction.
    """
    out = {
        "atr_ratio": None,
        "range_ratio": None,
        "vol_ratio": None,
        "coiled": False,
        "premove_score": None,
        "flags": [],
    }
    if len(historical) < 55:
        return out

    atr10 = _atr_abs(historical, 10)
    atr50 = _atr_abs(historical, 50)
    atr_ratio = (atr10 / atr50) if (atr10 and atr50 and atr50 > 0) else None

    def _avg_range_pct(window):
        rs = []
        for b in historical[:window]:
            c = float(b["close"]) or 0
            if c > 0:
                rs.append((float(b["high"]) - float(b["low"])) / c)
        return (sum(rs) / len(rs)) if rs else None

    r10 = _avg_range_pct(10)
    r50 = _avg_range_pct(50)
    range_ratio = (r10 / r50) if (r10 and r50 and r50 > 0) else None

    def _avg_vol(window):
        vs = [float(b["volume"]) for b in historical[:window] if b.get("volume")]
        return (sum(vs) / len(vs)) if vs else None

    v10 = _avg_vol(10)
    v50 = _avg_vol(50)
    vol_ratio = (v10 / v50) if (v10 and v50 and v50 > 0) else None

    out["atr_ratio"] = round(atr_ratio, 2) if atr_ratio is not None else None
    out["range_ratio"] = round(range_ratio, 2) if range_ratio is not None else None
    out["vol_ratio"] = round(vol_ratio, 2) if vol_ratio is not None else None

    contracting = 0
    if atr_ratio is not None and atr_ratio < 0.85:
        contracting += 1
        out["flags"].append(f"ATR contracting ({atr_ratio:.2f})")
    if range_ratio is not None and range_ratio < 0.85:
        contracting += 1
        out["flags"].append(f"range tightening ({range_ratio:.2f})")
    if vol_ratio is not None and vol_ratio < 0.85:
        contracting += 1
        out["flags"].append(f"volume dry-up ({vol_ratio:.2f})")

    out["coiled"] = contracting >= 2

    # Score: how far below 1.0 each ratio sits (capped), averaged -> 0-100.
    parts = []
    for r in (atr_ratio, range_ratio, vol_ratio):
        if r is not None:
            parts.append(max(0.0, min(1.0, (1.0 - r) / 0.5)))  # r=1.0->0, r<=0.5->1
    if parts:
        out["premove_score"] = round(sum(parts) / len(parts) * 100, 0)
    return out


def classify_stage(
    price: float,
    sma50: Optional[float],
    sma200: Optional[float],
    pct_from_52w_high: Optional[float],
) -> str:
    """Bucket the name by Weinstein-style stage to pick option direction.

    Returns one of: "stage2", "stage4", "neutral".

    - stage2 (CALL side): price > 50DMA > 200DMA AND within 25% of the 52w high.
      Clean uptrend -> debit-call-spread / bull-put / long-call candidate.
    - stage4 (PUT side): below the 200DMA OR more than 25% off the 52w high.
      Downtrend / breakdown -> debit-put-spread / bear-call / long-put candidate.
    - neutral: everything in between (chop / base-building) -> watch, no edge yet.
    """
    near_high = pct_from_52w_high is not None and pct_from_52w_high >= -25
    far_below_high = pct_from_52w_high is not None and pct_from_52w_high <= -25

    if sma50 is not None and sma200 is not None and price > sma50 > sma200 and near_high:
        return "stage2"

    below_200 = sma200 is not None and price < sma200
    if below_200 or far_below_high:
        return "stage4"

    return "neutral"
