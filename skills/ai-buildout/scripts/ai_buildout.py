#!/usr/bin/env python3
"""
ai_buildout — turn the AI-datacenter buildout into tradeable signals.

The buildout is a value chain (power -> grid -> cooling -> DC build -> compute ->
HBM -> optical -> semicap -> neocloud). This script maps each layer to tickers
(value_chain.yaml), finds the layer that is HEATING UP, tracks the capex that
funds the whole thing, and grades the macro thesis on the data we can measure.

It is the DETERMINISTIC data engine. The catalyst/WebSearch judgment (capex
guidance language, OOM checkpoints, thesis health narrative) is layered on top
by Claude at runtime per SKILL.md -- a python script cannot WebSearch.

All heavy lifting (technicals, IV, dealer gamma, options liquidity gate,
smart-money flow) is REUSED from the mover-screener skill by import, so a fix
there is inherited here (no duplicated clients).

Modes:
  bottleneck (default)  which layer is binding/heating + tradeable movers in it
  capex                 hyperscaler + neocloud capex scoreboard (leading signal)
  power                 power/grid/cooling chain screen (paper's headline gate)
  thesis                data-driven GREEN/YELLOW/RED scaffold + checkpoint list
"""

import argparse
import json
import os
import sys
from datetime import datetime
from statistics import mean

# --- Reuse the mover-screener machinery (single source of truth) -------------
# Resolve mover-screener/scripts in either context: a sibling skill inside this
# repo (skills/mover-screener/scripts) or an installed skill under ~/.claude.
_HERE = os.path.dirname(os.path.abspath(__file__))
_MS_CANDIDATES = [
    os.path.abspath(os.path.join(_HERE, "..", "..", "mover-screener", "scripts")),
    os.path.expanduser("~/.claude/skills/mover-screener/scripts"),
]
for _ms in _MS_CANDIDATES:
    if os.path.isdir(_ms):
        if _ms not in sys.path:
            sys.path.insert(0, _ms)
        break
import smart_money as sm  # noqa: E402
import ssl_setup  # noqa: F401  side-effecting: sets SSL_CERT_FILE via certifi
from alpaca_client import AlpacaOptionsClient  # noqa: E402
from fmp_client import FMPClient  # noqa: E402
from indicators import (  # noqa: E402
    atr_percent,
    classify_stage,
    pct_from_high,
    pct_return,
    premove_signal,
    sma,
)
from uw_client import UnusualWhalesClient  # noqa: E402

# --- Local taxonomy (the unique IP) ------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import value_chain as vc  # noqa: E402

POWER_LAYERS = ["power_generation", "grid_electrical", "cooling_thermal"]


# ---------------------------------------------------------------------------
# Per-ticker screen
# ---------------------------------------------------------------------------
def screen_ticker(sym: str, fmp: FMPClient, uw=None, want_iv: bool = False) -> dict:
    """Deterministic per-name read: stage, ATR%, coil, momentum, target, IV."""
    out = {"symbol": sym, "ok": False}
    profile = fmp.get_profile(sym)
    hist = fmp.get_historical(sym, 260)
    if not hist:
        out["error"] = "no history (off-plan or illiquid)"
        return out

    price = None
    if profile and profile.get("price"):
        price = float(profile["price"])
    if not price:
        price = float(hist[0].get("close") or 0) or None
    if not price:
        out["error"] = "no price"
        return out

    sma50 = sma(hist, 50)
    sma200 = sma(hist, 200)
    pfh = pct_from_high(hist)
    pm = premove_signal(hist)

    out.update(
        {
            "ok": True,
            "company": (profile or {}).get("companyName"),
            "price": round(price, 2),
            "beta": (profile or {}).get("beta"),
            "market_cap": (profile or {}).get("marketCap"),
            "stage": classify_stage(price, sma50, sma200, pfh),
            "atr_pct": atr_percent(hist, 14),
            "premove_score": pm.get("premove_score"),
            "coiled": pm.get("coiled"),
            "ret20": pct_return(hist, 20),
            "ret60": pct_return(hist, 60),
            "pct_from_high": pfh,
            "iv_rank": None,
            "target_avg": None,
            "upside_pct": None,
            "extended": None,
            "liquidity": None,
            "flow": None,
        }
    )

    pts = fmp.get_price_target_summary(sym)
    if pts:
        tgt = pts.get("lastQuarterAvgPriceTarget") or pts.get("lastMonthAvgPriceTarget")
        if tgt:
            out["target_avg"] = round(float(tgt), 2)
            out["upside_pct"] = round((float(tgt) - price) / price * 100, 1)
            out["extended"] = out["upside_pct"] < 0

    if want_iv and uw is not None and getattr(uw, "available", False):
        iv = uw.get_iv_rank(sym)
        out["iv_rank"] = round(iv, 0) if iv is not None else None

    return out


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _safe_mean(vals):
    vals = [v for v in vals if v is not None]
    return mean(vals) if vals else None


# ---------------------------------------------------------------------------
# Layer heat
# ---------------------------------------------------------------------------
def layer_heat(layer_key: str, screens: dict, flow_map: dict) -> dict:
    """Composite 0-100 'is this layer heating up' score from the screened names.

    heat = 0.35*stage2-tilt + 0.25*momentum + 0.20*coil + 0.20*bullish-flow
    Transparent and heuristic; the components are all reported so you can audit.
    """
    names = [screens[t] for t in vc.tickers_for(layer_key) if t in screens and screens[t].get("ok")]
    n = len(names)
    if n == 0:
        return {"layer": layer_key, "label": vc.layer_label(layer_key), "n": 0, "heat": None}

    n_s2 = sum(1 for s in names if s["stage"] == "stage2")
    n_s4 = sum(1 for s in names if s["stage"] == "stage4")
    avg_pm = _safe_mean([s["premove_score"] for s in names])
    avg_r20 = _safe_mean([s["ret20"] for s in names])

    bull_prem = bear_prem = 0.0
    for s in names:
        f = flow_map.get(s["symbol"])
        if not f:
            continue
        if f.get("direction") == "PUT":
            bear_prem += float(f.get("total_premium") or 0)
        else:
            bull_prem += float(f.get("total_premium") or 0)

    stage2_score = 100.0 * n_s2 / n
    momentum_score = _clamp(((avg_r20 or 0) + 20.0) / 40.0 * 100.0, 0, 100)
    coil_score = avg_pm if avg_pm is not None else 0.0
    flow_score = _clamp(bull_prem / 5_000_000.0 * 100.0, 0, 100)

    heat = 0.35 * stage2_score + 0.25 * momentum_score + 0.20 * coil_score + 0.20 * flow_score
    return {
        "layer": layer_key,
        "label": vc.layer_label(layer_key),
        "n": n,
        "n_stage2": n_s2,
        "n_stage4": n_s4,
        "avg_premove": round(avg_pm, 0) if avg_pm is not None else None,
        "avg_ret20": round(avg_r20, 1) if avg_r20 is not None else None,
        "bull_flow_prem": bull_prem,
        "bear_flow_prem": bear_prem,
        "stage2_score": round(stage2_score, 0),
        "momentum_score": round(momentum_score, 0),
        "coil_score": round(coil_score, 0),
        "flow_score": round(flow_score, 0),
        "heat": round(heat, 1),
    }


# ---------------------------------------------------------------------------
# Liquidity gate on a shortlist
# ---------------------------------------------------------------------------
def gate_names(names: list[dict], alpaca, max_spread_pct: float):
    if not getattr(alpaca, "available", False):
        for s in names:
            s["liquidity"] = {"passed": None, "reason": "gate unavailable"}
        return
    for s in names:
        opt = "put" if s["stage"] == "stage4" else "call"
        s["liquidity"] = alpaca.liquidity_gate(
            s["symbol"], s["price"], option_type=opt, max_spread_pct=max_spread_pct
        )


# ---------------------------------------------------------------------------
# Shared: screen the whole chain once + smart money once
# ---------------------------------------------------------------------------
def screen_chain(tickers, fmp, uw, want_iv=False):
    screens = {}
    for i, t in enumerate(tickers):
        print(f"  screening {i + 1}/{len(tickers)}: {t}", flush=True)
        screens[t] = screen_ticker(t, fmp, uw=uw, want_iv=want_iv)
    return screens


def discover_flow(uw, pages=8, min_premium=500_000.0):
    if not getattr(uw, "available", False):
        return {}
    print("  smart-money: pulling market-wide flow...", flush=True)
    cands = sm.discover(uw, pages=pages, min_premium=min_premium, top=60)
    print(f"  smart-money: {len(cands)} ranked flow candidates", flush=True)
    return sm.build_flow_map(cands)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _cap_str(cap):
    if not cap:
        return "n/a"
    cap = float(cap)
    if cap >= 1e12:
        return f"${cap / 1e12:.2f}T"
    if cap >= 1e9:
        return f"${cap / 1e9:.1f}B"
    return f"${cap / 1e6:.0f}M"


def _pct(x, d=1):
    return f"{x:+.{d}f}%" if isinstance(x, (int, float)) else "n/a"


def _liq_str(liq):
    if not liq or liq.get("passed") is None:
        return "?"
    return "PASS" if liq.get("passed") else "FAIL"


def _flow_str(s):
    f = s.get("flow")
    if not f:
        return ""
    return f" | flow:{f.get('label', '')}"


def name_row(s):
    stage = {"stage2": "CALL", "stage4": "PUT", "neutral": "neut"}.get(s["stage"], "?")
    ext = " EXTENDED" if s.get("extended") else ""
    coil = " COIL" if s.get("coiled") else ""
    iv = f" IV{int(s['iv_rank'])}" if s.get("iv_rank") is not None else ""
    return (
        f"  {s['symbol']:6s} {stage:4s} ${s['price']:<9.2f} "
        f"ATR{(s['atr_pct'] or 0):.0f}% pm{int(s['premove_score'] or 0):<3d} "
        f"r20 {_pct(s['ret20'], 0):>6s} tgt {_pct(s['upside_pct'], 0):>6s}"
        f"{iv} liq:{_liq_str(s.get('liquidity'))}{coil}{ext}{_flow_str(s)}"
    )


# ---------------------------------------------------------------------------
# MODE: capex
# ---------------------------------------------------------------------------
def mode_capex(fmp, args):
    proxies = vc.capex_proxies()
    funders = []
    for group in ("hyperscalers", "neocloud_capex"):
        for t in proxies.get(group, []):
            if t not in funders:
                funders.append(t)

    rows = []
    for t in funders:
        print(f"  capex: {t}", flush=True)
        cf = fmp.get_cash_flow(t, period="quarter", limit=6)
        if not cf:
            rows.append({"symbol": t, "error": "no cash-flow (off-plan)"})
            continue
        capex = [abs(float(r.get("capitalExpenditure") or 0)) for r in cf]
        dates = [r.get("date") for r in cf]
        latest = capex[0] if capex else 0
        qoq = ((capex[0] - capex[1]) / capex[1] * 100) if len(capex) > 1 and capex[1] else None
        yoy = ((capex[0] - capex[4]) / capex[4] * 100) if len(capex) > 4 and capex[4] else None
        ttm = sum(capex[:4]) if len(capex) >= 4 else None
        # Prefer YoY for trend; fall back to QoQ for young names with no year-ago Q.
        trend_basis = yoy if yoy is not None else qoq
        trend = (
            "RISING"
            if (trend_basis or 0) > 5
            else ("FALLING" if (trend_basis or 0) < -5 else "FLAT")
        )
        rows.append(
            {
                "symbol": t,
                "latest_q": dates[0] if dates else None,
                "latest_capex": latest,
                "qoq_pct": round(qoq, 1) if qoq is not None else None,
                "yoy_pct": round(yoy, 1) if yoy is not None else None,
                "ttm_capex": ttm,
                "trend": trend,
            }
        )

    rising = sum(1 for r in rows if r.get("trend") == "RISING")
    measured = sum(1 for r in rows if "error" not in r)
    verdict = (
        "EXPANDING" if measured and rising >= measured * 0.6 else ("MIXED" if rising else "COOLING")
    )
    return {"rows": rows, "rising": rising, "measured": measured, "verdict": verdict}


def render_capex(res) -> str:
    L = ["# AI-buildout — CAPEX scoreboard (leading indicator)", ""]
    L.append(
        f"**Funding verdict: {res['verdict']}**  "
        f"({res['rising']}/{res['measured']} funders raising capex YoY)"
    )
    L.append("")
    L.append("Capex from the hyperscalers + neoclouds is what funds the entire")
    L.append("value chain. Rising capex = demand pull-through to power, cooling,")
    L.append("compute, optical, semicap. Quarterly, most-recent first.")
    L.append("")
    L.append("| Name | Latest Q | Capex | QoQ | YoY | TTM | Trend |")
    L.append("|------|----------|-------|-----|-----|-----|-------|")
    for r in res["rows"]:
        if "error" in r:
            L.append(f"| {r['symbol']} | — | {r['error']} | | | | |")
            continue
        L.append(
            f"| {r['symbol']} | {r['latest_q']} | {_cap_str(r['latest_capex'])} | "
            f"{_pct(r['qoq_pct'], 0)} | {_pct(r['yoy_pct'], 0)} | "
            f"{_cap_str(r['ttm_capex'])} | {r['trend']} |"
        )
    L.append("")
    L.append("## Runtime WebSearch overlay (Claude fills at run time)")
    L.append("- Latest capex GUIDANCE language (raised / held / cut) per name.")
    L.append("- NVDA datacenter revenue trend + next-quarter guide.")
    L.append("- Read-through: which layer gets funded next (route to --mode bottleneck).")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# MODE: bottleneck / power (shared engine)
# ---------------------------------------------------------------------------
def mode_layers(fmp, uw, alpaca, args, layer_filter=None, title="BOTTLENECK"):
    layer_keys = layer_filter or vc.layer_keys()
    tickers = []
    for k in layer_keys:
        for t in vc.tickers_for(k):
            if t not in tickers:
                tickers.append(t)

    flow_map = discover_flow(uw, pages=args.flow_pages) if not args.no_flow else {}
    screens = screen_chain(tickers, fmp, uw, want_iv=False)
    for sym, s in screens.items():
        if s.get("ok") and sym in flow_map:
            s["flow"] = flow_map[sym]

    heats = [layer_heat(k, screens, flow_map) for k in layer_keys]
    heats = [h for h in heats if h.get("heat") is not None]
    heats.sort(key=lambda h: h["heat"], reverse=True)

    # Build tradeable shortlist from the hottest N layers.
    top = heats[: args.top_layers]
    shortlist = []
    for h in top:
        cands = [
            screens[t]
            for t in vc.tickers_for(h["layer"])
            if t in screens and screens[t].get("ok") and screens[t]["stage"] in ("stage2", "stage4")
        ]
        for c in cands:
            c.setdefault("_layers", [])
            if h["layer"] not in c["_layers"]:
                c["_layers"].append(h["layer"])
        shortlist.extend(cands)

    # de-dup (a name can sit in two top layers)
    seen, uniq = set(), []
    for c in shortlist:
        if c["symbol"] not in seen:
            seen.add(c["symbol"])
            uniq.append(c)
    uniq.sort(key=lambda s: (s.get("premove_score") or 0, s.get("ret20") or 0), reverse=True)

    if not args.no_liq:
        print(f"  liquidity gate on {len(uniq)} shortlisted names...", flush=True)
        gate_names(uniq, alpaca, args.max_spread_pct)

    if uw is not None and getattr(uw, "available", False) and not args.no_iv:
        for s in uniq:
            iv = uw.get_iv_rank(s["symbol"])
            s["iv_rank"] = round(iv, 0) if iv is not None else None

    return {"title": title, "heats": heats, "screens": screens, "shortlist": uniq}


def render_layers(res) -> str:
    L = [f"# AI-buildout — {res['title']} (which layer is heating up)", ""]
    L.append("Layer heat = 0.35·stage2-tilt + 0.25·momentum + 0.20·coil + 0.20·bull-flow.")
    L.append("Higher = the layer is binding / drawing money / breaking out.")
    L.append("")
    L.append("| Layer | Heat | n | S2 | S4 | avg r20 | avg coil | bull flow |")
    L.append("|-------|------|---|----|----|---------|----------|-----------|")
    for h in res["heats"]:
        L.append(
            f"| {h['label']} | **{h['heat']}** | {h['n']} | {h['n_stage2']} | "
            f"{h['n_stage4']} | {_pct(h['avg_ret20'], 0)} | "
            f"{int(h['avg_premove'] or 0)} | {_cap_str(h['bull_flow_prem'])} |"
        )
    L.append("")
    if res["heats"]:
        top = res["heats"][0]
        L.append(f"**Binding / hottest layer: {top['label']}** (heat {top['heat']}).")
        L.append("")
    L.append("## Tradeable movers in the hottest layer(s)")
    L.append("CALL = Stage 2 uptrend, PUT = Stage 4 breakdown. liq = options gate.")
    L.append("EXTENDED = trading above analyst consensus target (momentum-priced).")
    L.append("")
    passed = [s for s in res["shortlist"] if (s.get("liquidity") or {}).get("passed")]
    others = [s for s in res["shortlist"] if not (s.get("liquidity") or {}).get("passed")]
    L.append("### Liquidity PASS (tradeable now)")
    if passed:
        for s in passed:
            L.append(name_row(s))
    else:
        L.append("  (none cleared the gate)")
    L.append("")
    L.append("### Did not pass / unknown (equity-only or watch)")
    for s in others:
        L.append(name_row(s))
    L.append("")
    L.append("## Runtime WebSearch overlay (Claude fills at run time)")
    L.append("- Confirm the binding-layer read with current news (bottleneck_tells).")
    L.append("- Catalyst + next-earnings date per PASS name before structuring.")
    L.append("- Route winners into LOOP Step 4 (dossier) + Step 5 (defined-risk structure).")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# MODE: thesis
# ---------------------------------------------------------------------------
def mode_thesis(fmp, uw, alpaca, args):
    capex = mode_capex(fmp, args)
    layers = mode_layers(fmp, uw, alpaca, args, layer_filter=None, title="THESIS")
    heats = layers["heats"]
    power_heat = _safe_mean([h["heat"] for h in heats if h["layer"] in POWER_LAYERS])
    compute_heat = _safe_mean(
        [h["heat"] for h in heats if h["layer"] in ("compute_accelerators", "hbm_memory")]
    )

    # Market context: sector ETF 20d returns (tailwind/headwind).
    ctx = {}
    for etf in vc.market_context().get("sector_etfs", []) + [vc.market_context().get("benchmark")]:
        if not etf:
            continue
        s = screen_ticker(etf, fmp)
        ctx[etf] = s.get("ret20") if s.get("ok") else None

    # Data-driven preliminary light (Claude overlays WebSearch checkpoints).
    capex_ok = capex["verdict"] in ("EXPANDING", "MIXED")
    power_ok = (power_heat or 0) >= 40
    breadth_ok = (compute_heat or 0) >= 40
    score = sum([capex_ok, power_ok, breadth_ok])
    light = "GREEN" if score == 3 else ("YELLOW" if score == 2 else "RED")

    return {
        "capex": capex,
        "heats": heats,
        "power_heat": round(power_heat, 1) if power_heat is not None else None,
        "compute_heat": round(compute_heat, 1) if compute_heat is not None else None,
        "ctx": ctx,
        "light": light,
        "checks": {"capex_ok": capex_ok, "power_ok": power_ok, "breadth_ok": breadth_ok},
    }


def render_thesis(res) -> str:
    L = ["# AI-buildout — THESIS health (data-driven scaffold)", ""]
    L.append(f"## Preliminary light: **{res['light']}**  (warn-only; never auto-suppresses)")
    c = res["checks"]
    L.append("")
    L.append(
        f"- Capex funding: {'OK' if c['capex_ok'] else 'WEAK'} (verdict {res['capex']['verdict']})"
    )
    L.append(f"- Power chain heat: {'OK' if c['power_ok'] else 'WEAK'} ({res['power_heat']})")
    L.append(
        f"- Compute/HBM breadth: {'OK' if c['breadth_ok'] else 'WEAK'} ({res['compute_heat']})"
    )
    L.append("")
    L.append("### Market context (sector ETF 20d return)")
    for etf, r in res["ctx"].items():
        L.append(f"- {etf}: {_pct(r, 1)}")
    L.append("")
    L.append("### Layer heat snapshot")
    for h in sorted(res["heats"], key=lambda x: x["heat"], reverse=True):
        L.append(f"- {h['label']}: {h['heat']}")
    L.append("")
    L.append("## Thesis checkpoints — Claude grades these via WebSearch at run time")
    L.append("(see references/buildout_thesis.md for the falsifiers)")
    L.append("- Is hyperscaler capex GUIDANCE still rising (not just trailing capex)?")
    L.append("- Is power still the binding constraint, or has it loosened?")
    L.append(
        "- Any efficiency shock (a cheap frontier model) that breaks the compute-demand trend?"
    )
    L.append("- Are the OOM/scaling checkpoints still on the paper's trendline?")
    L.append("- Any policy/export/financing shock to the buildout?")
    L.append("")
    L.append("**Rule: a RED data scaffold + RED WebSearch read should DOWN-WEIGHT AI")
    L.append("names in LOOP Step 2 — warn the user, do not auto-block trades.**")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_out(mode, md, payload, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    md_path = os.path.join(out_dir, f"ai_buildout_{mode}_{ts}.md")
    json_path = os.path.join(out_dir, f"ai_buildout_{mode}_{ts}.json")
    with open(md_path, "w") as f:
        f.write(md + "\n")
    with open(json_path, "w") as f:
        json.dump(
            {"mode": mode, "ts": ts, "vc_version": vc.version(), **payload},
            f,
            indent=2,
            default=str,
        )
    return md_path, json_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="AI-buildout value-chain signal engine")
    p.add_argument(
        "--mode", choices=["bottleneck", "capex", "power", "thesis"], default="bottleneck"
    )
    p.add_argument(
        "--output-dir",
        default=os.path.join(
            os.environ.get("CAMPAIGN_DIR", "."), "reports", f"ai_buildout_{datetime.now():%Y%m%d}"
        ),
        help="where reports are written (default: ./reports, or $CAMPAIGN_DIR/reports if set)",
    )
    p.add_argument(
        "--top-layers",
        type=int,
        default=2,
        help="how many hottest layers to pull tradeable names from",
    )
    p.add_argument(
        "--flow-pages", type=int, default=8, help="UW market-flow pages for smart-money discovery"
    )
    p.add_argument("--max-spread-pct", type=float, default=10.0)
    p.add_argument("--no-flow", action="store_true", help="skip smart-money discovery")
    p.add_argument("--no-liq", action="store_true", help="skip options liquidity gate")
    p.add_argument("--no-iv", action="store_true", help="skip IV-rank overlay")
    p.add_argument("--api-key", default=None)
    args = p.parse_args()

    print(
        f"ai-buildout :: mode={args.mode} :: value_chain v{vc.version()} "
        f"({len(vc.all_tickers())} names)",
        flush=True,
    )

    fmp = FMPClient(api_key=args.api_key)
    uw = UnusualWhalesClient()
    alpaca = AlpacaOptionsClient()
    if not uw.available:
        print("  NOTE: UW unavailable — IV/flow overlays limited", flush=True)
    if not alpaca.available:
        print("  NOTE: Alpaca unavailable — liquidity gate limited", flush=True)

    if args.mode == "capex":
        res = mode_capex(fmp, args)
        md = render_capex(res)
        payload = res
    elif args.mode == "power":
        res = mode_layers(fmp, uw, alpaca, args, layer_filter=POWER_LAYERS, title="POWER")
        md = render_layers(res)
        payload = {"heats": res["heats"], "shortlist": res["shortlist"]}
    elif args.mode == "thesis":
        res = mode_thesis(fmp, uw, alpaca, args)
        md = render_thesis(res)
        payload = res
    else:  # bottleneck
        res = mode_layers(fmp, uw, alpaca, args, title="BOTTLENECK")
        md = render_layers(res)
        payload = {"heats": res["heats"], "shortlist": res["shortlist"]}

    md_path, json_path = write_out(args.mode, md, payload, args.output_dir)
    stats = fmp.get_api_stats()
    print("\n" + "=" * 70)
    print(md)
    print("=" * 70)
    print(
        f"\nFMP calls: {stats['api_calls_made']} | off-plan skipped: {stats['plan_skipped_count']}"
    )
    print(f"Saved:\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
