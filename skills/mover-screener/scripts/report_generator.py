#!/usr/bin/env python3
"""
Report generator for the Mover Screener.

Emits a JSON file (structured) and a Markdown file (human-readable) with two
ranked tables -- CALL candidates (Stage 2 uptrends) and PUT candidates
(Stage 4 downtrends) -- each carrying beta, ATR%, trend, distance from the 52w
high, IV rank, squeeze flags, the liquidity-gate verdict, and a one-line thesis.
"""

import json


def _liq_cell(liq: dict) -> str:
    """Compact liquidity-gate cell for the summary tables."""
    passed = liq.get("passed")
    spread = liq.get("spread_pct")
    if passed is True:
        return f"PASS ({spread:.0f}%)" if spread is not None else "PASS"
    if passed is False:
        return f"FAIL ({spread:.0f}%)" if spread is not None else "FAIL"
    return "n/a"


def _iv_cell(stock: dict) -> str:
    ivr = stock.get("iv_rank")
    if ivr is None:
        return "n/a"
    tag = stock.get("iv_tag", "")
    short = {"low": "lo", "high": "hi", "mid": ""}.get(tag, "")
    return f"{ivr:.0f}{(' ' + short) if short else ''}"


def _squeeze_cell(stock: dict) -> str:
    sq = stock.get("squeeze", {})
    level = sq.get("squeeze_level", "low")
    return {"high": "HIGH", "moderate": "mod", "low": "-"}.get(level, level)


def _trend_cell(stock: dict) -> str:
    r20 = stock.get("ret20")
    r60 = stock.get("ret60")
    r20s = f"{r20:+.0f}%" if r20 is not None else "?"
    r60s = f"{r60:+.0f}%" if r60 is not None else "?"
    return f"{r20s}/{r60s}"


def _table(rows: list[dict], side: str) -> list[str]:
    lines = []
    lines.append(
        "| # | Symbol | Beta | ATR% | 20d/60d | %52wH | IVR | Squeeze | Liquidity | Thesis |"
    )
    lines.append(
        "|---|--------|------|------|---------|-------|-----|---------|-----------|--------|"
    )
    for i, s in enumerate(rows, 1):
        beta = s.get("beta")
        atr = s.get("atr_pct")
        pfh = s.get("pct_from_high")
        lines.append(
            f"| {i} | {s['symbol']} | {beta:.1f} | {atr:.1f}% | {_trend_cell(s)} | "
            f"{pfh:+.0f}% | {_iv_cell(s)} | {_squeeze_cell(s)} | {_liq_cell(s.get('liquidity', {}))} | "
            f"{s.get('thesis', '')} |"
        )
    if not rows:
        lines.append(f"| - | _no {side} candidates_ | | | | | | | | |")
    lines.append("")
    return lines


def _detail(stock: dict) -> list[str]:
    lines = []
    name = stock.get("company_name", "")
    lines.append(f"### {stock['symbol']} - {name}")
    price = stock.get("price", 0) or 0
    mcap = stock.get("market_cap", 0) or 0
    mcap_s = f"${mcap / 1e9:.1f}B" if mcap >= 1e9 else (f"${mcap / 1e6:.0f}M" if mcap else "n/a")
    lines.append(
        f"**{stock.get('direction', '')}** | Price ${price:.2f} | {mcap_s} | "
        f"{stock.get('sector', 'n/a')} / {stock.get('industry', 'n/a')}"
    )
    lines.append("")

    pb = stock.get("profile_beta")
    rb = stock.get("realized_beta")
    beta_src = []
    if pb is not None:
        beta_src.append(f"profile {pb:.2f}")
    if rb is not None:
        beta_src.append(f"realized {rb:.2f}")
    lines.append(
        f"- Beta (effective): **{stock.get('beta'):.2f}** "
        f"({', '.join(beta_src) if beta_src else 'n/a'})"
    )
    lines.append(
        f"- ATR% (14d): **{stock.get('atr_pct'):.1f}%**  |  Swing score: {stock.get('swing_score', 0):.1f}"
    )

    sma50 = stock.get("sma50")
    sma200 = stock.get("sma200")
    sma50s = f"${sma50:.2f}" if sma50 else "n/a"
    sma200s = f"${sma200:.2f}" if sma200 else "n/a"
    lines.append(
        f"- Trend: 50DMA {sma50s}, 200DMA {sma200s}, {stock.get('pct_from_high'):+.0f}% from 52w high"
    )
    r20 = stock.get("ret20")
    r60 = stock.get("ret60")
    lines.append(
        f"- Momentum: 20d {r20:+.1f}% / 60d {r60:+.1f}%"
        if r20 is not None and r60 is not None
        else "- Momentum: n/a"
    )

    ivr = stock.get("iv_rank")
    if ivr is not None:
        lines.append(f"- IV rank: {ivr:.0f} ({stock.get('iv_bias', '')})")

    sq = stock.get("squeeze", {})
    if sq.get("squeeze_flags"):
        lines.append(f"- Squeeze ({sq.get('squeeze_level')}): {', '.join(sq['squeeze_flags'])}")
    elif sq.get("squeeze_level"):
        lines.append(f"- Squeeze: {sq.get('squeeze_level')} (no flags)")

    liq = stock.get("liquidity", {})
    lines.append(
        f"- Liquidity gate: **{_liq_cell(liq)}** "
        f"({liq.get('expiration', 'n/a')} ~{liq.get('dte', '?')}DTE, "
        f"strike {liq.get('strike', 'n/a')}) - {liq.get('reason', '')}"
    )
    lines.append(f"- **Thesis:** {stock.get('thesis', '')}")
    lines.append("")
    return lines


def generate_reports(
    calls: list[dict],
    puts: list[dict],
    metadata: dict,
    json_file: str,
    md_file: str,
) -> None:
    # ---- JSON ----
    report = {
        "schema_version": "1.0",
        "metadata": metadata,
        "call_candidates": calls,
        "put_candidates": puts,
    }
    with open(json_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  JSON report saved to: {json_file}")

    # ---- Markdown ----
    lines = []
    lines.append("# Mover Screener - High-Beta Secondary Movers")
    lines.append(f"**Generated:** {metadata.get('generated_at', 'n/a')}")
    lines.append(
        f"**Profile screen:** beta >= {metadata.get('min_beta')} "
        f"AND ATR% >= {metadata.get('min_atr_pct')}%  |  "
        f"**Direction:** {metadata.get('direction')}"
    )
    lines.append("")
    lines.append(
        "> The target is a STYLE, not a sector: high-beta names with wide ATR and "
        "liquid monthly options that throw big directional swings (for calls AND puts)."
    )
    lines.append("")

    funnel = metadata.get("funnel", {})
    lines.append("## Screening Funnel")
    lines.append("")
    lines.append("| Stage | Count |")
    lines.append("|-------|-------|")
    lines.append(f"| Universe (screener + curated) | {funnel.get('universe', 'n/a')} |")
    lines.append(f"| Beta pre-filter pool | {funnel.get('beta_pool', 'n/a')} |")
    lines.append(f"| Passed profile (beta + ATR) | {funnel.get('profile_passed', 'n/a')} |")
    lines.append(f"| CALL candidates | {len(calls)} |")
    lines.append(f"| PUT candidates | {len(puts)} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append(f"## CALL Candidates - Stage 2 Uptrends ({len(calls)})")
    lines.append("")
    lines.extend(_table(calls, "CALL"))
    lines.append(f"## PUT Candidates - Stage 4 Downtrends ({len(puts)})")
    lines.append("")
    lines.extend(_table(puts, "PUT"))

    lines.append("---")
    lines.append("")
    lines.append("## Detail")
    lines.append("")
    if calls:
        lines.append("### CALL side")
        lines.append("")
        for s in calls:
            lines.extend(_detail(s))
    if puts:
        lines.append("### PUT side")
        lines.append("")
        for s in puts:
            lines.extend(_detail(s))

    lines.append("---")
    lines.append("")
    lines.append("## Legend")
    lines.append("")
    lines.append("- **Beta**: effective = max(FMP profile beta, realized beta vs SPY).")
    lines.append("- **ATR%**: 14-day average true range as % of price (daily swing size).")
    lines.append("- **20d/60d**: trailing price returns (momentum / extension).")
    lines.append("- **%52wH**: distance from the 52-week high.")
    lines.append(
        "- **IVR**: 1y IV rank (lo <30 = buy vol / long option bias; hi >60 = sell vol / spread bias)."
    )
    lines.append(
        "- **Squeeze**: HIGH/mod from borrow fee rate, shares available, short interest % float."
    )
    lines.append(
        "- **Liquidity**: ATM monthly-option gate. PASS = two-sided quote with spread <= 10% of mid."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "**Disclaimer:** Educational and informational purposes only. Not investment "
        "advice. High-beta names carry outsized risk; verify earnings dates, size down "
        "for wider stops, and confirm option liquidity before trading. Do your own research."
    )
    lines.append("")

    with open(md_file, "w") as f:
        f.write("\n".join(lines))
    print(f"  Markdown report saved to: {md_file}")
