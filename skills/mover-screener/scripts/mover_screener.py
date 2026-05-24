#!/usr/bin/env python3
"""
Mover Screener - hunt high-beta secondary movers market-wide.

Surfaces the second- and third-derivative names (APLD, IREN, NBIS, LITE, DXCM,
SPOT type) that throw big directional swings for BOTH calls and puts -- not the
crowded, lower-beta mega-caps. The target is a STYLE (high beta + wide ATR +
liquid monthly options), so discovery is profile-based and spans every sector.

Pipeline:
  Phase 1  Universe  : finvizfinance Technical screener (free, default) OR
                       FINVIZ Elite when FINVIZ_API_KEY is set, prefiltered to
                       Beta>=~min, AvgVol>=1M, Price>$10. Plus optional
                       --universe-file curated names merged in.
  Phase 2  Profile   : per name FMP 260d history -> ATR%(14) + 20/50/200 DMA +
                       52w distance + 20d/60d returns; beta from /stable/profile
                       (with realized-beta + FINVIZ-beta fallbacks). Keep
                       beta>=--min-beta AND ATR%>=--min-atr-pct. Bucket Stage 2
                       (CALL) vs Stage 4 (PUT).
  Phase 3  Overlays  : UW squeeze + IV rank, Alpaca monthly liquidity gate (on
                       the direction we'd trade), on the ranked shortlist only.

Usage:
    python3 scripts/mover_screener.py
        [--universe-source finviz_free|finviz_elite] [--min-beta 1.5]
        [--min-atr-pct 5] [--direction both|long|short] [--universe-file PATH]
        [--max-names 25] [--output-dir reports/]

Output:
    JSON    : mover_screener_YYYY-MM-DD_HHMMSS.json
    Markdown: mover_screener_YYYY-MM-DD_HHMMSS.md
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

# IMPORTANT: import ssl_setup FIRST so certifi's CA bundle is in the environment
# before finvizfinance / requests build their SSL contexts (macOS would
# otherwise raise SSL: CERTIFICATE_VERIFY_FAILED).
import ssl_setup  # noqa: F401  (side-effecting import)
from alpaca_client import AlpacaOptionsClient
from fmp_client import FMPClient
from indicators import (
    atr_percent,
    classify_stage,
    pct_from_high,
    pct_return,
    premove_signal,
    realized_beta,
    sma,
)
from report_generator import generate_reports
from universe_source import get_universe
from uw_client import UnusualWhalesClient

US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NASDAQ Global Select", "NYSE American"}


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Mover Screener - high-beta secondary movers for options"
    )
    parser.add_argument("--api-key", help="FMP API key (defaults to FMP_API_KEY env var)")
    parser.add_argument(
        "--universe-source",
        choices=["finviz_free", "finviz_elite"],
        default="finviz_free",
        help="Universe backend. finviz_free (default, no key). finviz_elite "
        "activates only when FINVIZ_API_KEY is set, else falls back to free.",
    )
    parser.add_argument(
        "--min-beta", type=float, default=1.5, help="Minimum effective beta (default: 1.5)"
    )
    parser.add_argument(
        "--min-atr-pct", type=float, default=5.0, help="Minimum 14d ATR%% (default: 5.0)"
    )
    parser.add_argument(
        "--direction",
        choices=["both", "long", "short"],
        default="both",
        help="long=CALL bucket only, short=PUT bucket only, both=both (default: both)",
    )
    parser.add_argument(
        "--universe-file",
        help="File with extra symbols to merge in (e.g. trading_campaign/UNIVERSE.md). "
        "One-per-line or comma/space separated; markdown bullets tolerated.",
    )
    parser.add_argument(
        "--max-names",
        type=int,
        default=25,
        help="Max names to deep-analyze + report PER direction (default: 25)",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=300,
        help="Max universe candidates to pull histories for, ranked by beta (default: 300)",
    )
    parser.add_argument(
        "--max-spread-pct",
        type=float,
        default=10.0,
        help="Liquidity gate: max ATM bid/ask spread as %% of mid (default: 10.0)",
    )
    parser.add_argument(
        "--no-overlays",
        action="store_true",
        help="Skip UW + Alpaca overlays (faster, profile screen only)",
    )
    parser.add_argument(
        "--smart-money",
        action="store_true",
        help="Also run the smart-money pre-positioning discovery screen (market-wide "
        "UW flow + dealer gamma), write its own report, and annotate the shortlist "
        "with any matching smart-money flow lean.",
    )
    parser.add_argument("--output-dir", default="reports/", help="Output directory for reports")
    args = parser.parse_args()

    if args.min_beta < 0:
        parser.error("--min-beta must be >= 0")
    if args.min_atr_pct < 0:
        parser.error("--min-atr-pct must be >= 0")
    if args.max_names < 1:
        parser.error("--max-names must be >= 1")
    return args


def iv_classify(iv_rank: Optional[float]) -> tuple[str, str]:
    """Return (tag, human bias) for an IV rank."""
    if iv_rank is None:
        return "", ""
    if iv_rank < 30:
        return "low", "low IV - long-option / buy-vol bias"
    if iv_rank > 60:
        return "high", "high IV - debit/credit-spread / sell-vol bias"
    return "mid", "mid IV - either structure works"


def build_thesis(stock: dict) -> str:
    """One-line setup thesis from stage, extension, IV, and squeeze."""
    direction = stock["direction"]
    pfh = stock.get("pct_from_high")
    ret60 = stock.get("ret60")
    iv_tag = stock.get("iv_tag", "")
    sq = stock.get("squeeze", {})
    parts = []

    pfh_s = f"{pfh:+.0f}%" if pfh is not None else "n/a"
    if direction == "CALL":
        parts.append(f"Stage 2 uptrend, {pfh_s} from 52w high")
        if ret60 is not None and ret60 >= 40:
            parts.append(f"extended (+{ret60:.0f}%/60d) - prefer pullback or spread")
    else:
        parts.append(f"Stage 4 downtrend, {pfh_s} from 52w high")
        if ret60 is not None and ret60 <= -25:
            parts.append(f"breaking down ({ret60:.0f}%/60d)")

    if iv_tag == "low":
        parts.append("low IV favors long options")
    elif iv_tag == "high":
        parts.append("high IV favors spreads")

    if sq.get("squeeze_level") == "high":
        parts.append("high squeeze - violent swings")
    elif sq.get("squeeze_flags"):
        parts.append(sq["squeeze_flags"][0])

    # Pre-move coil (about-to-move trigger): flag tight names primed to release.
    if stock.get("coiled"):
        ps = stock.get("premove_score")
        parts.append(f"COILED pre-move{f' ({ps:.0f})' if ps is not None else ''}")

    # Smart-money flow lean from the market-wide UW feed (set in overlay phase).
    fl = stock.get("flow") or {}
    if fl.get("label"):
        parts.append(fl["label"])

    liq = stock.get("liquidity", {})
    if liq.get("passed") is False:
        parts.append("ILLIQUID options")

    return "; ".join(parts)


def main():
    args = parse_arguments()

    print("=" * 70)
    print("Mover Screener - High-Beta Secondary Movers")
    print("=" * 70)

    try:
        fmp = FMPClient(api_key=args.api_key)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Phase 1: Universe (finvizfinance free / Elite, + curated merge)
    # ------------------------------------------------------------------
    print("\nPhase 1: Universe")
    print("-" * 70)
    print(f"  Requested backend: {args.universe_source}", flush=True)
    candidates, backend = get_universe(
        source=args.universe_source,
        min_beta=args.min_beta,
        min_avg_vol=1_000_000,
        min_price=10.0,
        max_rows=max(args.scan_limit * 2, 400),
        universe_file=args.universe_file,
    )
    print(f"  Universe backend used: {backend}")
    print(f"  Candidates returned: {len(candidates)}")
    if not candidates:
        print(
            "ERROR: empty universe. Is finvizfinance installed and certifi present?",
            file=sys.stderr,
        )
        sys.exit(1)

    # Index by symbol; carry the FINVIZ prefilter fields (beta/atr as fallback).
    fv = {c["symbol"].upper(): c for c in candidates}

    # Rank by FINVIZ beta desc (None last) then cap the scan, but always keep
    # curated/universe-file names even if they sit outside the prefilter.
    def fv_beta(sym):
        return fv.get(sym, {}).get("beta") or 0.0

    curated = [c["symbol"].upper() for c in candidates if c.get("source") == "universe_file"]
    scannable = [c["symbol"].upper() for c in candidates if c.get("source") != "universe_file"]
    scannable.sort(key=fv_beta, reverse=True)
    pool = scannable[: args.scan_limit]
    for sym in curated:
        if sym not in pool:
            pool.append(sym)
    print(f"  Scan pool (ranked by beta, + curated): {len(pool)}")

    # ------------------------------------------------------------------
    # Phase 2: Profile filter (beta + ATR%) and stage bucketing
    # ------------------------------------------------------------------
    print("\nPhase 2: Profile filter (beta + ATR%)")
    print("-" * 70)
    print("  Fetching SPY 260d history (realized-beta baseline)...", end=" ", flush=True)
    spy_hist = fmp.get_historical("SPY", days=260)
    print(f"{len(spy_hist)} days" if spy_hist else "FAILED (realized beta limited)")

    passed = []
    for i, sym in enumerate(pool):
        if (i + 1) % 25 == 0 or i == len(pool) - 1:
            print(f"    Progress: {i + 1}/{len(pool)}", flush=True)

        hist = fmp.get_historical(sym, days=260)
        if not hist or len(hist) < 60:
            continue

        atr = atr_percent(hist, 14)
        if atr is None:
            continue

        # FINVIZ ATR% as a fallback if FMP history is too short for ATR.
        fvc = fv.get(sym, {})
        if atr is None:
            atr = fvc.get("atr_pct")
        if atr is None:
            continue

        # Beta: PREFER authoritative /stable/profile beta; fall back to realized
        # beta vs SPY, then to the FINVIZ value, only when the prior is missing.
        # (Not max(): FINVIZ betas for thin micro-caps are wildly inflated -- e.g.
        # XNDU reads 16 on FINVIZ but 2.8 on FMP -- and max() would let that noise
        # dominate the swing-score ranking and crowd out real movers.)
        prof = fmp.get_profile(sym)
        profile_beta = prof.get("beta") if prof else None
        rbeta = realized_beta(hist, spy_hist) if spy_hist else None
        fv_b = fvc.get("beta")
        eff_beta = (
            profile_beta
            if profile_beta is not None
            else (rbeta if rbeta is not None else (fv_b or 0.0))
        )
        if eff_beta < args.min_beta or atr < args.min_atr_pct:
            continue

        price = float(hist[0]["close"])
        market_cap = prof.get("marketCap") if prof else None
        sector = (prof.get("sector") if prof else None) or "Unknown"
        industry = (prof.get("industry") if prof else None) or "Unknown"
        company_name = (prof.get("companyName") if prof else None) or sym

        sma50 = sma(hist, 50)
        sma200 = sma(hist, 200)
        pfh = pct_from_high(hist)
        stage = classify_stage(price, sma50, sma200, pfh)
        if stage == "neutral":
            continue

        direction = "CALL" if stage == "stage2" else "PUT"
        if args.direction == "long" and direction != "CALL":
            continue
        if args.direction == "short" and direction != "PUT":
            continue

        r20 = pct_return(hist, 20)
        r60 = pct_return(hist, 60)
        s20 = sma(hist, 20)
        premove = premove_signal(hist)
        passed.append(
            {
                "symbol": sym,
                "company_name": company_name,
                "sector": sector,
                "industry": industry,
                "price": round(price, 2),
                "market_cap": market_cap,
                "profile_beta": round(profile_beta, 2) if profile_beta else None,
                "realized_beta": round(rbeta, 2) if rbeta is not None else None,
                "finviz_beta": round(fv_b, 2) if fv_b is not None else None,
                "beta": round(eff_beta, 2),
                "atr_pct": round(atr, 1),
                "sma20": round(s20, 2) if s20 else None,
                "sma50": round(sma50, 2) if sma50 else None,
                "sma200": round(sma200, 2) if sma200 else None,
                "pct_from_high": round(pfh, 1) if pfh is not None else None,
                "ret20": round(r20, 1) if r20 is not None else None,
                "ret60": round(r60, 1) if r60 is not None else None,
                "stage": stage,
                "direction": direction,
                "swing_score": round(eff_beta * atr, 1),
                "premove": premove,
                "coiled": premove.get("coiled", False),
                "premove_score": premove.get("premove_score"),
            }
        )

    print(f"  Passed profile screen: {len(passed)}")

    calls = sorted(
        [s for s in passed if s["direction"] == "CALL"],
        key=lambda x: x["swing_score"],
        reverse=True,
    )[: args.max_names]
    puts = sorted(
        [s for s in passed if s["direction"] == "PUT"],
        key=lambda x: x["swing_score"],
        reverse=True,
    )[: args.max_names]
    shortlist = calls + puts

    # ------------------------------------------------------------------
    # Phase 3: Overlays (UW squeeze + IV, Alpaca liquidity gate)
    # ------------------------------------------------------------------
    if not args.no_overlays and shortlist:
        print("\nPhase 3: Overlays (squeeze / IV / liquidity gate)")
        print("-" * 70)
        uw = UnusualWhalesClient()
        alpaca = AlpacaOptionsClient()
        if not uw.available:
            print("  UW unavailable - squeeze/IV overlays skipped")
        if not alpaca.available:
            print("  Alpaca unavailable - liquidity gate skipped")

        for i, s in enumerate(shortlist):
            print(f"    Overlay {i + 1}/{len(shortlist)}: {s['symbol']}", flush=True)
            s["squeeze"] = uw.squeeze_overlay(s["symbol"]) if uw.available else {}
            iv_rank = uw.get_iv_rank(s["symbol"]) if uw.available else None
            s["iv_rank"] = round(iv_rank, 0) if iv_rank is not None else None
            s["iv_tag"], s["iv_bias"] = iv_classify(iv_rank)
            opt_type = "put" if s["direction"] == "PUT" else "call"
            s["liquidity"] = (
                alpaca.liquidity_gate(
                    s["symbol"],
                    s["price"],
                    option_type=opt_type,
                    max_spread_pct=args.max_spread_pct,
                )
                if alpaca.available
                else {"passed": None, "reason": "gate unavailable"}
            )
            s["thesis"] = build_thesis(s)
        uw_calls = uw.api_calls_made
        alpaca_calls = alpaca.api_calls_made
    else:
        for s in shortlist:
            s.setdefault("squeeze", {})
            s.setdefault("iv_rank", None)
            s.setdefault("iv_tag", "")
            s.setdefault("iv_bias", "")
            s.setdefault("liquidity", {"passed": None, "reason": "overlays skipped"})
            s["thesis"] = build_thesis(s)
        uw_calls = alpaca_calls = 0

    # ------------------------------------------------------------------
    # Smart-money pre-positioning discovery (market-wide UW flow + gamma)
    # ------------------------------------------------------------------
    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if args.smart_money:
        print("\nSmart-money pre-positioning discovery")
        print("-" * 70)
        import smart_money as sm

        sm_uw = UnusualWhalesClient()
        if not sm_uw.available:
            print("  UW unavailable - smart-money screen skipped")
        else:
            alerts = sm_uw.get_market_flow_alerts(pages=8)
            print(f"  Flow alerts scanned: {len(alerts)}")
            agg = sm.aggregate_flow(alerts)
            sm_cands = sm.rank_candidates(agg, min_premium=500_000.0, top=30)
            sm_fmp = FMPClient(api_key=args.api_key)
            sm_alpaca = AlpacaOptionsClient()
            sm.enrich(
                sm_cands,
                sm_fmp,
                sm_alpaca,
                sm_uw,
                cap_ceiling=100e9,
                min_beta=1.5,
                gate_per_tier=12,
                max_spread_pct=args.max_spread_pct,
            )
            sm_json = os.path.join(args.output_dir, f"smart_money_{timestamp}.json")
            sm_md = os.path.join(args.output_dir, f"smart_money_{timestamp}.md")
            sm.write_report(
                sm_cands,
                sm_json,
                sm_md,
                meta={
                    "alerts_scanned": len(alerts),
                    "pages": 8,
                    "cap_ceiling": 100e9,
                    "min_beta": 1.5,
                },
            )
            # Cross-annotate the mover shortlist with any matching flow lean.
            flow_map = sm.build_flow_map(sm_cands)
            for s in shortlist:
                fm = flow_map.get(s["symbol"])
                if fm:
                    s["flow"] = fm
                    s["thesis"] = build_thesis(s)
            passes = sum(1 for c in sm_cands if c.get("liquidity", {}).get("passed") is True)
            print(
                f"  Smart-money candidates: {len(sm_cands)} "
                f"(liquidity PASS {passes}/{min(20, len(sm_cands))} enriched)"
            )
            print(f"  Report: {sm_md}")

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    print("\nGenerating reports")
    print("-" * 70)
    json_file = os.path.join(args.output_dir, f"mover_screener_{timestamp}.json")
    md_file = os.path.join(args.output_dir, f"mover_screener_{timestamp}.md")

    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe_source_requested": args.universe_source,
        "universe_backend_used": backend,
        "min_beta": args.min_beta,
        "min_atr_pct": args.min_atr_pct,
        "direction": args.direction,
        "max_names": args.max_names,
        "funnel": {
            "universe": len(candidates),
            "beta_pool": len(pool),
            "profile_passed": len(passed),
        },
        "api_stats": {
            "fmp_calls": fmp.api_calls_made,
            "uw_calls": uw_calls,
            "alpaca_calls": alpaca_calls,
        },
    }

    generate_reports(calls, puts, metadata, json_file, md_file)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("Mover Screening Complete")
    print("=" * 70)
    print(f"\n  Universe backend used: {backend}")
    print(f"  CALL candidates: {len(calls)}   PUT candidates: {len(puts)}")
    if calls:
        print("\n  Top CALL:")
        for s in calls[:5]:
            print(
                f"    {s['symbol']:6} [{s.get('sector', '?')[:14]:14}] beta {s['beta']:.1f} "
                f"ATR {s['atr_pct']:.0f}% {s.get('pct_from_high', 0):+.0f}%52wH  "
                f"liq:{_liq_short(s.get('liquidity', {}))}"
            )
    if puts:
        print("\n  Top PUT:")
        for s in puts[:5]:
            print(
                f"    {s['symbol']:6} [{s.get('sector', '?')[:14]:14}] beta {s['beta']:.1f} "
                f"ATR {s['atr_pct']:.0f}% {s.get('pct_from_high', 0):+.0f}%52wH  "
                f"liq:{_liq_short(s.get('liquidity', {}))}"
            )
    print(f"\n  JSON:     {json_file}")
    print(f"  Markdown: {md_file}")
    print(f"\n  API calls - FMP: {fmp.api_calls_made}, UW: {uw_calls}, Alpaca: {alpaca_calls}")
    skipped = sorted(fmp.plan_skipped)
    if skipped:
        preview = ", ".join(skipped[:10]) + ("…" if len(skipped) > 10 else "")
        print(
            f"  FMP off-plan symbols skipped (expected on broad scans): {len(skipped)} ({preview})"
        )
    print()


def _liq_short(liq: dict) -> str:
    p = liq.get("passed")
    return "PASS" if p is True else ("FAIL" if p is False else "n/a")


if __name__ == "__main__":
    main()
