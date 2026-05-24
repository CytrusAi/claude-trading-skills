---
name: ai-buildout
description: Turn the AI-datacenter buildout into tradeable options signals via a value-chain map (power, grid, cooling, datacenter build, compute, HBM, optical, semicap, neocloud). Finds the layer that is binding/heating up and surfaces the movers inside it, tracks the hyperscaler capex that funds the whole chain (leading indicator), and grades whether the buildout thesis is still intact. Use when the user wants AI-buildout / datacenter / power / compute / capex-cycle exposure, asks "which part of the AI chain is hot right now," wants the secondary names behind the AI capex wave, or wants a thesis-health check on the AI supercycle. Reuses the mover-screener engine (technicals, IV, dealer gamma, options liquidity gate, smart-money flow) so signals are consistent with the rest of the campaign.
---

# AI Buildout — value-chain signal engine

The AI-datacenter buildout is a stack. Each layer throws option-sized moves when
IT becomes the binding constraint, and the constraint rotates upward over time
(chips → memory → networking → power). This skill maps each layer to tickers,
finds which layer is heating up, tracks the capex that funds the whole thing, and
grades the thesis. It is the campaign's lens on the single biggest theme.

It is a DETERMINISTIC data engine plus a runtime WebSearch overlay that you
(Claude) add. The python script cannot WebSearch; it produces the measurable
signals and a checklist, and you layer the catalyst/guidance/thesis judgment on
top, exactly like LOOP Step 4.

## When to use

- User wants AI-buildout / datacenter / power / compute / capex-cycle exposure
- "Which part of the AI chain is hot right now?" / "where's the bottleneck?"
- User wants the secondary names behind the AI capex wave (not just NVDA)
- User wants a thesis-health check on the AI supercycle before sizing into it
- Feeds LOOP Step 2 (theme/flow) and Step 3 (idea-gen); winners go to Step 4/5

## Prerequisites (all reused from mover-screener)

- `FMP_API_KEY` — technicals, capex (cash-flow), price targets, grades (`/stable/` only)
- `UNUSUAL_WHALES_API_KEY` (optional) — IV rank, dealer gamma, market-wide flow
- `ALPACA_API_KEY` / `ALPACA_API_SECRET` (optional) — options liquidity gate
- `PyYAML` — reads the taxonomy
- Keys live in `~/.zshrc`; `source ~/.zshrc` before running in a fresh shell.

The script imports the mover-screener clients directly
(`~/.claude/skills/mover-screener/scripts`), so any fix there is inherited. No
duplicated clients.

## The taxonomy (the IP)

`scripts/value_chain.yaml` — the layer → ticker map, capex proxies (who funds
it), and bottleneck "tells" (sold-out / lead-time language to confirm via
WebSearch). Edit this file to add/remove names; bump `version`. Current layers:
power generation, grid/electrical, cooling/thermal, datacenter build, compute/
accelerators, HBM/memory, networking/optical, semicap, neocloud/hosting.

## Modes

Run from `scripts/`:

```bash
source ~/.zshrc
python3 ai_buildout.py --mode bottleneck   # default
python3 ai_buildout.py --mode capex
python3 ai_buildout.py --mode power
python3 ai_buildout.py --mode thesis
```

### `--mode bottleneck` (default)
Screens every name in the chain once, scores each layer's "heat"
(0.35·stage2-tilt + 0.25·momentum + 0.20·coil + 0.20·bull-flow), ranks layers,
then pulls tradeable CALL/PUT names from the hottest `--top-layers` (default 2),
runs the options liquidity gate, and annotates smart-money flow + IV. Output:
"binding layer = X; tradeable PASS names = …".

### `--mode capex`
The leading indicator. Pulls quarterly capex (FMP cash-flow) for the
hyperscalers + neoclouds, computes QoQ/YoY/TTM and a funding verdict
(EXPANDING/MIXED/COOLING). Cheap (~7 calls). Rising capex = demand pull-through
down the chain.

### `--mode power`
Same engine restricted to power generation + grid + cooling — the paper's
headline binding constraint. Use when the question is specifically "power."

### `--mode thesis`
Composes capex + full-chain layer heat + sector-ETF context into a preliminary
GREEN/YELLOW/RED light, then lists the thesis checkpoints for you to grade.
Warn-only: a RED read down-weights AI names in Step 2, never auto-blocks a trade.

### Useful flags
`--top-layers N`, `--flow-pages N`, `--max-spread-pct`, `--no-flow`, `--no-liq`,
`--no-iv` (drop overlays for a fast/cheap structural run), `--output-dir`.

## Your runtime overlay (do this after running the script)

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

## Reads from / interacts with

- **mover-screener** — shares all clients/indicators; this is the AI-themed lens
  on the same machinery (mover-screener is profile/style-based, market-wide).
- **LOOP.md** — feeds Step 2/3; can be added to `sunday_stage.py` as an optional
  stage (capex + bottleneck are the cheap, high-signal ones to stage weekly).
- **vcp-screener** — cross-check a buildout CALL name for a VCP base.

## Guardrails (inherits campaign rules)

- Never places orders. Liquidity gate mandatory before "tradeable."
- Names above analyst target are flagged EXTENDED, not hidden.
- Defined-risk framing; respect IV-rank routing (>70 → spreads).
- `--mode thesis` is allowed to say "stop trading this theme" (warn-only).
- Trade the capex trendline, not the AGI-date prophecy (see thesis reference).
