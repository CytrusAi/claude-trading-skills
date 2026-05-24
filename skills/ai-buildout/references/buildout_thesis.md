# AI-buildout thesis — checkpoints & falsifiers

The trade lens behind this skill: the AI-datacenter buildout is a multi-year
capex supercycle, and the binding constraint rotates up the value chain (chips →
memory → networking → power). We want to own whichever layer is binding and step
aside if the supercycle breaks. This file is what `--mode thesis` grades against
via WebSearch at run time. Source intuition: Aschenbrenner "Situational
Awareness" (2024), read critically (the author has an investment-firm incentive
and is over-confident on timelines).

## What keeps the thesis intact (GREEN)
1. **Capex still climbing.** Hyperscaler + neocloud capex guidance rising, not
   just trailing capex. `--mode capex` measures trailing; WebSearch confirms the
   GUIDANCE direction (raised / held / cut on the last call).
2. **Power is the gate.** Demand for GW of firm power outruns supply
   (interconnect queues, PPAs, nuclear restarts, gas-turbine backlogs).
3. **Compute demand un-sated.** NVDA datacenter revenue still growing; next-gen
   parts sold out / allocated.
4. **Scaling still pays.** Each new model generation justifies the spend (no
   plateau in capability per OOM of compute).

## What would falsify it (move toward RED)
- **Efficiency shock.** A cheap frontier model (DeepSeek-style) that delivers
  top capability at a fraction of the compute → breaks the "more compute = moat"
  demand curve. This is the #1 risk to the whole chain.
- **Capex cut.** Any hyperscaler GUIDING capex flat/down, or walking back DC
  commitments → demand pull-through to power/cooling/optical evaporates first.
- **Power loosens.** If power stops being the gate (faster interconnects, demand
  softening), the power-chain premium deflates.
- **Digestion phase.** Buildout pauses to absorb capacity (utilization dips,
  semicap orders roll over) → semicap + neocloud roll first.
- **Financing/policy shock.** Rates, export controls, or a credit event that
  raises the cost of the buildout.

## OOM / scaling checkpoints (the paper's trendline)
The paper's core claim is ~0.5 OOM/year of effective compute (raw compute +
algorithmic efficiency + "unhobbling"). Grade whether reality is still on that
line:
- Are frontier training runs still scaling ~order-of-magnitude every ~2 years?
- Is algorithmic efficiency still compounding (not stalled)?
- Are the labs still guiding to bigger clusters (the "trillion-dollar cluster"
  trajectory), or pulling back?
- Skeptic's note: timeline claims (AGI-by-2027, intelligence explosion) are the
  weakest, most incentive-loaded part. Trade the capex trendline, not the
  AGI-date prophecy.

## How the light maps
`--mode thesis` sets a preliminary light from data it can measure:
- capex verdict (EXPANDING/MIXED = ok)
- power-chain heat ≥ 40
- compute/HBM breadth ≥ 40

GREEN = 3/3, YELLOW = 2/3, RED = ≤1/3. Then Claude overlays the WebSearch
checkpoints above and writes the final call. **Warn-only:** a RED read
down-weights AI names in LOOP Step 2; it never auto-blocks a trade.
