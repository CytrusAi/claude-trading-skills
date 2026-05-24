#!/usr/bin/env python3
"""
Smart-Money Pre-Positioning -- discovery from the market-wide options flow.

Instead of checking flow on a name we already found (per-finalist confirmation),
this SCANS the UW unusual-flow feed market-wide to FIND tickers where smart money
is positioning BEFORE the move: aggressive opening sweeps + dealer gamma building.

Pipeline:
  1. Pull N pages of the UW market-wide flow-alerts feed (newest first).
  2. Drop index/ETF tickers and stale/closing noise; aggregate per ticker.
  3. Score each ticker's conviction (aggressive OPENING premium, sweeps, vol/OI)
     and assign a direction (call-dominant -> CALL, put-dominant -> PUT).
  4. (caller) run the top candidates through the FMP profile + Alpaca liquidity
     gate, and optionally confirm dealer gamma is building.

Signals that mark "real" pre-positioning (vs noise):
  - all_opening_trades=True  -> NEW exposure (not closing)
  - has_sweep=True           -> urgency (swept the offer/bid across exchanges)
  - ask-side premium (calls) / bid-side premium (puts) -> aggressive directional
  - high volume_oi_ratio     -> today's volume dwarfs existing OI = fresh bets
  - next_earnings_date soon  -> a scheduled catalyst to release the move
"""

from collections import defaultdict

# Index / broad-ETF tickers to exclude from single-name discovery.
_EXCLUDE = {
    "SPX",
    "SPXW",
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VIX",
    "VIXW",
    "NDX",
    "RUT",
    "TLT",
    "HYG",
    "LQD",
    "GLD",
    "SLV",
    "USO",
    "UNG",
    "XLF",
    "XLE",
    "XLK",
    "XLV",
    "XLI",
    "XLY",
    "XLP",
    "XLU",
    "XLB",
    "XLRE",
    "XLC",
    "SMH",
    "SOXL",
    "TQQQ",
    "SQQQ",
    "UVXY",
    "VXX",
    "EEM",
    "EFA",
    "FXI",
    "ARKK",
    "KWEB",
}


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def aggregate_flow(alerts: list[dict], opening_only: bool = True) -> dict:
    """Aggregate raw flow alerts into per-ticker conviction metrics.

    `opening_only` no longer hard-filters (UW rarely flags all_opening_trades);
    instead opening premium is tracked as a bonus and high volume/OI ratio is the
    primary 'fresh positioning' proxy.
    """
    agg: dict = defaultdict(
        lambda: {
            "ticker": None,
            "call_prem": 0.0,
            "put_prem": 0.0,
            "open_prem": 0.0,
            "sweeps": 0,
            "alerts": 0,
            "max_voi": 0.0,
            "earnings": None,
            "sector": None,
        }
    )
    for a in alerts:
        t = (a.get("ticker") or "").upper()
        if not t or t in _EXCLUDE:
            continue
        prem = _f(a.get("total_premium"))
        d = agg[t]
        d["ticker"] = t
        d["alerts"] += 1
        if a.get("has_sweep"):
            d["sweeps"] += 1
        if a.get("all_opening_trades"):
            d["open_prem"] += prem
        voi = _f(a.get("volume_oi_ratio"))
        if voi > d["max_voi"]:
            d["max_voi"] = voi
        if a.get("next_earnings_date"):
            d["earnings"] = a.get("next_earnings_date")
        if a.get("sector"):
            d["sector"] = a.get("sector")
        if a.get("type") == "call":
            d["call_prem"] += prem
        else:
            d["put_prem"] += prem
    return agg


def rank_candidates(agg: dict, min_premium: float = 250_000.0, top: int = 40) -> list[dict]:
    """Turn aggregates into ranked, directional candidates.

    direction  = call-premium vs put-premium dominance.
    conviction = dominant-side premium, boosted by sweeps (urgency), volume/OI
                 (freshness), and confirmed-opening premium.
    """
    out = []
    for t, d in agg.items():
        total_prem = d["call_prem"] + d["put_prem"]
        if total_prem < min_premium:
            continue
        net_dir = d["call_prem"] - d["put_prem"]
        direction = "CALL" if net_dir >= 0 else "PUT"
        dom_prem = d["call_prem"] if direction == "CALL" else d["put_prem"]
        sweep_boost = 1.0 + 0.03 * d["sweeps"]
        voi_boost = 1.0 + min(d["max_voi"], 100.0) / 100.0  # up to 2x for fresh bets
        open_boost = 1.0 + (d["open_prem"] / total_prem if total_prem else 0.0)
        conviction = dom_prem * sweep_boost * voi_boost * open_boost
        # directional lopsidedness (how one-sided the bet is): 0.5-1.0
        lopsided = abs(net_dir) / total_prem if total_prem else 0.0
        out.append(
            {
                "symbol": t,
                "direction": direction,
                "net_dir_prem": round(net_dir, 0),
                "total_premium": round(total_prem, 0),
                "opening_premium": round(d["open_prem"], 0),
                "call_prem": round(d["call_prem"], 0),
                "put_prem": round(d["put_prem"], 0),
                "lopsided_pct": round(lopsided * 100, 0),
                "sweeps": d["sweeps"],
                "alerts": d["alerts"],
                "max_voi": round(d["max_voi"], 1),
                "earnings": d["earnings"],
                "sector": d["sector"],
                "conviction": round(conviction, 0),
            }
        )
    out.sort(key=lambda x: x["conviction"], reverse=True)
    return out[:top]


def flow_label(c: dict) -> str:
    """One-line smart-money tag for the mover-screener thesis line."""
    side = "bullish" if c["direction"] == "CALL" else "bearish"
    prem_m = c["total_premium"] / 1e6
    sw = f", {c['sweeps']} sweeps" if c["sweeps"] else ""
    return f"smart-money {side} flow (${prem_m:.1f}M{sw})"


def discover(
    uw, pages: int = 6, min_premium: float = 250_000.0, top: int = 40, opening_only: bool = True
) -> list[dict]:
    """End-to-end discovery: pull feed -> aggregate -> rank. `uw` is an
    UnusualWhalesClient. Returns ranked directional candidates (no profile/
    liquidity gate yet -- the caller applies those)."""
    if not getattr(uw, "available", False):
        return []
    alerts = uw.get_market_flow_alerts(pages=pages)
    if not alerts:
        return []
    agg = aggregate_flow(alerts, opening_only=opening_only)
    return rank_candidates(agg, min_premium=min_premium, top=top)


def build_flow_map(candidates: list[dict]) -> dict:
    """symbol -> {direction,label,...} for cross-referencing in the mover overlay."""
    m = {}
    for c in candidates:
        m[c["symbol"]] = {
            "direction": c["direction"],
            "total_premium": c["total_premium"],
            "sweeps": c["sweeps"],
            "label": flow_label(c),
        }
    return m


def enrich(
    candidates,
    fmp,
    alpaca,
    uw,
    cap_ceiling=100e9,
    min_beta=1.5,
    gate_per_tier=12,
    max_spread_pct=10.0,
):
    """Two-tier enrichment.

    Step 1 (cheap, ALL candidates): FMP profile -> beta, market cap, price,
    sector; classify each into a tier:
      - "secondary" : cap <= cap_ceiling AND (beta unknown OR beta >= min_beta)
                      -> the PRIMARY list (high-beta secondary movers / small caps)
      - "mega"      : cap > cap_ceiling  -> shown in a separate mega-cap section
      - "lowbeta"   : cap <= ceiling but beta < min_beta -> not a mover, demoted
    Step 2 (Alpaca/UW, top of each FEATURED tier only): monthly liquidity gate on
    the dominant direction + dealer-gamma-building.
    """
    for c in candidates:
        sym = c["symbol"]
        prof = fmp.get_profile(sym) if fmp else None
        price = beta = cap = None
        if prof:
            c["sector"] = c.get("sector") or prof.get("sector")
            c["company_name"] = prof.get("companyName") or sym
            price = prof.get("price")
            beta = prof.get("beta")
            cap = prof.get("marketCap")
        if (not price or not cap) and fmp:
            q = fmp.get_quote(sym)
            if q:
                price = price or q.get("price")
                cap = cap or q.get("marketCap")
        c["price"] = round(price, 2) if price else None
        c["beta"] = round(beta, 2) if isinstance(beta, (int, float)) else None
        c["market_cap"] = cap
        if c["beta"] is None and not cap:
            # Unqueryable on this plan (FMP 402 for foreign/index/special tickers):
            # no beta AND no cap. Don't let it pollute the primary tier.
            c["tier"] = "unknown"
        elif cap and cap > cap_ceiling:
            c["tier"] = "mega"
        elif c["beta"] is None or c["beta"] >= min_beta:
            c["tier"] = "secondary"
        else:
            c["tier"] = "lowbeta"
        c.setdefault("liquidity", {"passed": None, "reason": "not gated"})
        c.setdefault("gamma", {"building": False, "trend": None})

    # Step 2: gate the top of each featured tier (secondary first = primary).
    for tier in ("secondary", "mega"):
        for c in [x for x in candidates if x.get("tier") == tier][:gate_per_tier]:
            price = c.get("price")
            opt_type = "put" if c["direction"] == "PUT" else "call"
            if alpaca and getattr(alpaca, "available", False) and price:
                c["liquidity"] = alpaca.liquidity_gate(
                    c["symbol"],
                    price,
                    option_type=opt_type,
                    max_spread_pct=max_spread_pct,
                )
            if uw and getattr(uw, "available", False):
                c["gamma"] = uw.gamma_building(c["symbol"])
    return candidates


def _cap_str(cap):
    if not cap:
        return "-"
    if cap >= 1e12:
        return f"${cap / 1e12:.1f}T"
    if cap >= 1e9:
        return f"${cap / 1e9:.0f}B"
    return f"${cap / 1e6:.0f}M"


def _tier_table(cands):
    def liq(c):
        lq = c.get("liquidity", {})
        p = lq.get("passed")
        sp = lq.get("spread_pct")
        if p is True:
            return f"PASS{f' {sp:.0f}%' if sp is not None else ''}"
        if p is False:
            return f"FAIL{f' {sp:.0f}%' if sp is not None else ''}"
        return "-"

    rows = [
        "| # | Sym | Dir | Beta | Cap | Total Prem | Lopsided | Sweeps | Vol/OI | Gamma | Liquidity | Sector | Earnings |",
        "|---|-----|-----|------|-----|-----------|----------|--------|--------|-------|-----------|--------|----------|",
    ]
    for i, c in enumerate(cands, 1):
        g = c.get("gamma", {})
        gflag = "building" if g.get("building") else (g.get("trend") or "-")
        beta = c.get("beta")
        rows.append(
            f"| {i} | {c['symbol']} | {c['direction']} | "
            f"{beta if beta is not None else '-'} | {_cap_str(c.get('market_cap'))} | "
            f"${c['total_premium'] / 1e6:.1f}M | {c.get('lopsided_pct', 0):.0f}% | "
            f"{c['sweeps']} | {c['max_voi']:.0f} | {gflag} | {liq(c)} | "
            f"{str(c.get('sector') or '-')[:16]} | {c.get('earnings') or '-'} |"
        )
    return rows


def write_report(candidates, json_file, md_file, meta=None):
    """Dual JSON + Markdown report, TWO-TIER: secondary movers (primary) first,
    then a separate mega-cap flow section."""
    import json
    from datetime import datetime

    meta = meta or {}
    with open(json_file, "w") as f:
        json.dump({"meta": meta, "candidates": candidates}, f, indent=2, default=str)

    secondary = [c for c in candidates if c.get("tier") == "secondary"]
    mega = [c for c in candidates if c.get("tier") == "mega"]
    lowbeta = [c for c in candidates if c.get("tier") == "lowbeta"]
    unknown = [c for c in candidates if c.get("tier") == "unknown"]
    ceiling = meta.get("cap_ceiling", 100e9)

    lines = [
        "# Smart-Money Pre-Positioning — Discovery from Market-Wide Options Flow",
        f"**Generated:** {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "> Tickers where smart money is positioning BEFORE the move: aggressive "
        "sweeps + fresh volume/OI + (where checked) dealer gamma building. "
        "Liquidity gate applied on the dominant direction. Verify earnings vs expiry.",
        "",
        f"Feed alerts scanned: {meta.get('alerts_scanned', '?')} | ranked: {len(candidates)} | "
        f"secondary {len(secondary)} / mega {len(mega)} / low-beta {len(lowbeta)}"
        f"{f' / unqueryable {len(unknown)}' if unknown else ''}",
        "",
        f"## TIER 1 — Secondary Movers (PRIMARY)  ·  cap ≤ {_cap_str(ceiling)}, beta ≥ "
        f"{meta.get('min_beta', 1.5)}",
        "*The high-beta secondary movers / smaller caps we hunt. Lead here.*",
        "",
    ]
    lines += _tier_table(secondary) if secondary else ["_none this run_"]
    lines += [
        "",
        f"## TIER 2 — Mega-Cap Flow  ·  cap > {_cap_str(ceiling)}",
        "*Big-name positioning, shown for context (crowded, lower beta). Don't lead here.*",
        "",
    ]
    lines += _tier_table(mega) if mega else ["_none this run_"]
    if lowbeta:
        lines += [
            "",
            f"_({len(lowbeta)} smaller-cap but low-beta names "
            f"({', '.join(c['symbol'] for c in lowbeta[:12])}"
            f"{'…' if len(lowbeta) > 12 else ''}) filtered from the primary list — "
            "not movers.)_",
        ]
    lines += [
        "",
        "## How to read",
        "- **Dir**: call-premium vs put-premium dominance (CALL = bullish positioning).",
        "- **Beta/Cap**: tiering keys — Tier 1 is our secondary-mover style.",
        "- **Lopsided**: how one-sided the bet is (100% = entirely one direction).",
        "- **Sweeps**: aggressive multi-exchange fills (urgency).",
        "- **Vol/OI**: today's volume vs existing open interest (high = fresh bets).",
        "- **Gamma**: dealer net-gamma trend (building = dealers accumulating → coil).",
        "- **Liquidity**: Alpaca ATM monthly gate on the dominant side; lead with PASS.",
        "",
        "_Discovery only — not advice. Confirm the technical setup + earnings date before trading._",
    ]
    with open(md_file, "w") as f:
        f.write("\n".join(lines))


def _main():
    import argparse
    import os
    import sys
    from datetime import datetime

    sys.path.insert(0, os.path.dirname(__file__))
    import ssl_setup  # noqa: F401
    from alpaca_client import AlpacaOptionsClient
    from fmp_client import FMPClient
    from uw_client import UnusualWhalesClient

    ap = argparse.ArgumentParser(description="Smart-money pre-positioning discovery")
    ap.add_argument("--pages", type=int, default=8, help="UW feed pages (200/page)")
    ap.add_argument("--min-premium", type=float, default=500_000.0)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument(
        "--cap-ceiling", type=float, default=100e9, help="Tier-1 market-cap ceiling (default $100B)"
    )
    ap.add_argument("--min-beta", type=float, default=1.5, help="Tier-1 minimum beta (default 1.5)")
    ap.add_argument(
        "--gate-per-tier",
        type=int,
        default=12,
        help="Liquidity/gamma gate this many per featured tier",
    )
    ap.add_argument("--max-spread-pct", type=float, default=10.0)
    ap.add_argument("--output-dir", default="reports/")
    args = ap.parse_args()

    uw = UnusualWhalesClient()
    if not uw.available:
        print("ERROR: UNUSUAL_WHALES_API_KEY required for smart-money screen.", file=sys.stderr)
        sys.exit(1)
    print("Smart-money discovery: pulling market-wide flow feed...", flush=True)
    alerts = uw.get_market_flow_alerts(pages=args.pages)
    print(f"  {len(alerts)} alerts scanned")
    agg = aggregate_flow(alerts)
    cands = rank_candidates(agg, min_premium=args.min_premium, top=args.top)
    print(f"  {len(cands)} ranked candidates; enriching + tiering...", flush=True)
    fmp = FMPClient()
    alpaca = AlpacaOptionsClient()
    enrich(
        cands,
        fmp,
        alpaca,
        uw,
        cap_ceiling=args.cap_ceiling,
        min_beta=args.min_beta,
        gate_per_tier=args.gate_per_tier,
        max_spread_pct=args.max_spread_pct,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    jf = os.path.join(args.output_dir, f"smart_money_{ts}.json")
    mf = os.path.join(args.output_dir, f"smart_money_{ts}.md")
    write_report(
        cands,
        jf,
        mf,
        meta={
            "alerts_scanned": len(alerts),
            "pages": args.pages,
            "min_premium": args.min_premium,
            "cap_ceiling": args.cap_ceiling,
            "min_beta": args.min_beta,
        },
    )
    print(f"\n  JSON: {jf}\n  Markdown: {mf}")
    sec = sum(1 for c in cands if c.get("tier") == "secondary")
    mega = sum(1 for c in cands if c.get("tier") == "mega")
    print(f"  Tier 1 secondary movers: {sec} | Tier 2 mega-cap: {mega}")


if __name__ == "__main__":
    _main()
