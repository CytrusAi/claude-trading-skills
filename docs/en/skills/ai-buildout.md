---
layout: default
title: "Ai Buildout"
grand_parent: English
parent: Skill Guides
nav_order: 11
lang_peer: /ja/skills/ai-buildout/
permalink: /en/skills/ai-buildout/
generated: true
---

# Ai Buildout
{: .no_toc }

Turn the AI-datacenter buildout into tradeable options signals via a value-chain map (power, grid, cooling, datacenter build, compute, HBM, optical, semicap, neocloud). Finds the layer that is binding/heating up and surfaces the movers inside it, tracks the hyperscaler capex that funds the whole chain (leading indicator), and grades whether the buildout thesis is still intact. Use when the user wants AI-buildout / datacenter / power / compute / capex-cycle exposure, asks "which part of the AI chain is hot right now," wants the secondary names behind the AI capex wave, or wants a thesis-health check on the AI supercycle. Reuses the mover-screener engine (technicals, IV, dealer gamma, options liquidity gate, smart-money flow) so signals are consistent with the rest of the campaign.
{: .fs-6 .fw-300 }

<span class="badge badge-api">FMP Required</span>

[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/ai-buildout){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

# AI Buildout — value-chain signal engine

---

## 2. When to Use

- User wants AI-buildout / datacenter / power / compute / capex-cycle exposure
- "Which part of the AI chain is hot right now?" / "where's the bottleneck?"
- User wants the secondary names behind the AI capex wave (not just NVDA)
- User wants a thesis-health check on the AI supercycle before sizing into it
- Feeds LOOP Step 2 (theme/flow) and Step 3 (idea-gen); winners go to Step 4/5

---

## 3. Prerequisites

- `FMP_API_KEY` — technicals, capex (cash-flow), price targets, grades (`/stable/` only)
- `UNUSUAL_WHALES_API_KEY` (optional) — IV rank, dealer gamma, market-wide flow
- `ALPACA_API_KEY` / `ALPACA_API_SECRET` (optional) — options liquidity gate
- `PyYAML` — reads the taxonomy
- Keys live in `~/.zshrc`; `source ~/.zshrc` before running in a fresh shell.

The script imports the mover-screener clients directly
(`~/.claude/skills/mover-screener/scripts`), so any fix there is inherited. No
duplicated clients.

---

## 4. Quick Start

The script prints + saves markdown/JSON to
`~/Desktop/trading_campaign/reports/ai_buildout_<date>/`. Then YOU add:

1. **Confirm the binding layer** with current news against that layer's
   `bottleneck_tells` (sold out, lead times, backlog, allocation, PPAs).
2. **Capex guidance** — for `--mode capex`, WebSearch the latest guidance
   language (raised/held/cut) + NVDA datacenter revenue + next-quarter guide.
3. **Catalyst + next-earnings date** for each liquidity-PASS name before any
   structure (a 3-month expiry through a print is a binary, flag it).
4. **Thesis grade** — for `--mode thesis`, grade the checkpoints in

---

## 5. Workflow

The script prints + saves markdown/JSON to
`~/Desktop/trading_campaign/reports/ai_buildout_<date>/`. Then YOU add:

1. **Confirm the binding layer** with current news against that layer's
   `bottleneck_tells` (sold out, lead times, backlog, allocation, PPAs).
2. **Capex guidance** — for `--mode capex`, WebSearch the latest guidance
   language (raised/held/cut) + NVDA datacenter revenue + next-quarter guide.
3. **Catalyst + next-earnings date** for each liquidity-PASS name before any
   structure (a 3-month expiry through a print is a binary, flag it).
4. **Thesis grade** — for `--mode thesis`, grade the checkpoints in
   `references/buildout_thesis.md` via WebSearch and write the final call.
5. **Route to LOOP** — winners into Step 4 (dossier) + Step 5 (defined-risk
   structure, IV-rank routed). Never place orders; surface candidates only.

---

## 6. Resources

**References:**

- `skills/ai-buildout/references/buildout_thesis.md`

**Scripts:**

- `skills/ai-buildout/scripts/ai_buildout.py`
- `skills/ai-buildout/scripts/value_chain.py`
