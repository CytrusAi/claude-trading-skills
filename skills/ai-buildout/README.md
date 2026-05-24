# AI Buildout + Mover Screener — two Claude Code skills

This is a **two-skill bundle**. They share the same data clients and work
together, but each stands on its own:

- **`ai-buildout`** — the AI-datacenter buildout lens. Maps the whole value chain
  (power → grid → cooling → datacenter build → compute → HBM → optical → semicap
  → neocloud) to tickers, finds the layer that's *binding* (heating up) right
  now, tracks the hyperscaler capex that funds the chain, and grades whether the
  buildout thesis is still intact.
- **`mover-screener`** — the market-wide engine underneath it. Hunts high-beta
  "secondary mover" stocks (the APLD / IREN / NBIS / LITE type) across every
  sector, buckets them into CALL (uptrend) and PUT (breakdown) candidates, and
  liquidity-gates them. `ai-buildout` imports its data clients, so you need both
  installed.

The short version: **mover-screener finds movers by *style* (high beta, wide ATR,
liquid options) across the whole market. ai-buildout finds them by *theme* (where
in the AI buildout the money is flowing).** Same plumbing, different lens.

Most of this README covers `ai-buildout`. Mover-screener has its own
[dedicated section below](#companion-skill-mover-screener).

---

## Table of contents

- [What it does](#what-it-does)
- [How it works (the design)](#how-it-works-the-design)
- [The value chain](#the-value-chain)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [API keys](#api-keys)
- [Usage — the four modes](#usage--the-four-modes)
- [Flags reference](#flags-reference)
- [Example run-through](#example-run-through)
- [What Claude adds at run time](#what-claude-adds-at-run-time)
- [Customizing the taxonomy](#customizing-the-taxonomy)
- [Output files](#output-files)
- [Companion skill: mover-screener](#companion-skill-mover-screener)
- [How the two skills fit together](#how-the-two-skills-fit-together)
- [Guardrails and limits](#guardrails-and-limits)
- [Credits](#credits)

---

## What it does

The AI buildout is a stack. Each layer throws option-sized moves when *it*
becomes the binding constraint, and the constraint rotates upward over time
(chips → memory → networking → power). This skill answers four questions:

1. **Which layer is hot right now?** (`--mode bottleneck`) — ranks all nine
   layers by a transparent "heat" score and surfaces the tradeable movers inside
   the hottest ones, each pre-checked for option liquidity.
2. **Is the money still flowing in?** (`--mode capex`) — pulls quarterly capex
   for the hyperscalers and neoclouds (the leading indicator) and gives a funding
   verdict.
3. **What's happening with power?** (`--mode power`) — the same engine focused on
   the power / grid / cooling layers, the headline binding constraint.
4. **Is the thesis still intact?** (`--mode thesis`) — composes capex + layer
   heat + sector context into a GREEN/YELLOW/RED read so you know when to step
   aside.

---

## How it works (the design)

The skill is split in two on purpose:

- **A deterministic Python engine** (`ai_buildout.py`) produces everything you
  can measure from market data: stage classification, ATR%, volatility coil,
  momentum, IV rank, dealer gamma, options liquidity, smart-money flow, capex
  trends, analyst targets. Same number every time.
- **A runtime WebSearch overlay** that Claude adds on top: capex guidance
  language, catalyst and earnings dates, news confirmation of the bottleneck, and
  the final thesis call. A Python script can't browse the web, so this judgment
  layer is done by Claude when you invoke the skill (the same split as a normal
  research workflow).

The engine **reuses** the screening machinery from the `mover-screener` skill by
importing it directly (technicals, IV, gamma, the options liquidity gate, the
smart-money flow scanner). Nothing is copied. A fix in mover-screener is
inherited here. That's why mover-screener is a hard prerequisite (see below).

The only genuinely new code here is the value-chain taxonomy. That's the IP.

---

## The value chain

Defined in `scripts/value_chain.yaml` (fully editable):

| Layer | What it is | Example tickers |
|-------|-----------|-----------------|
| Power generation | gas turbines, nuclear, IPPs | VST, CEG, GEV, OKLO, SMR, TLN |
| Grid / electrical | transformers, switchgear, cabling | ETN, PWR, GEV, NVT, HUBB, POWL |
| Cooling / thermal | liquid cooling, thermal management | VRT, MOD, COMT, BWXT |
| Datacenter build | construction, MEP, REITs | FIX, PWR, EME, DLR, SRE |
| Compute / accelerators | GPUs, custom silicon | NVDA, AMD, AVGO, MRVL, TSM |
| HBM / memory | high-bandwidth memory | MU, WDC, STX |
| Networking / optical | switches, optics, transceivers | ANET, CRDO, COHR, LITE, ALAB |
| Semicap / equipment | fab, test, probe equipment | UCTT, FORM, ICHR, KLAC, LRCX |
| Neocloud / hosting | GPU-as-a-service, miner pivots | CRWV, NBIS, APLD, IREN, CORZ |

Each layer also carries `bottleneck_tells` (sold-out / lead-time phrases to
confirm via news) and the file tracks `capex_proxies` (who funds the buildout).

---

## Prerequisites

**Required**

- **Claude Code** (this is a skill, invoked from inside Claude Code).
- **Python 3.9+** with **PyYAML** (`pip install pyyaml`).
- **The `mover-screener` skill installed** at
  `~/.claude/skills/mover-screener/`. This skill imports its clients
  (`fmp_client`, `uw_client`, `alpaca_client`, `indicators`, `smart_money`,
  `ssl_setup`). Without it, the engine won't run. Make sure your copy of
  `mover-screener/scripts/fmp_client.py` includes the methods `get_cash_flow`,
  `get_price_target_summary`, and `get_grades_consensus` (add them if your copy
  predates this skill).
- **An FMP (Financial Modeling Prep) API key.** The engine uses only `/stable/`
  endpoints. A Starter plan is enough for what this skill needs (cash-flow,
  price-target-summary, grades-consensus, profile, historical, quote). It does
  NOT need the paywalled transcripts / press-release endpoints.

**Optional but recommended** (the skill degrades gracefully without them)

- **Unusual Whales API key** — adds IV rank, dealer-gamma building, and the
  market-wide smart-money flow scan. Without it, those overlays are skipped.
- **Alpaca API keys** (paper account is fine) — powers the options liquidity
  gate. Without it, names show `liq:?` instead of PASS/FAIL.

---

## Installation

1. Copy the skill folder into your Claude Code skills directory:

   ```
   ~/.claude/skills/ai-buildout/
     SKILL.md
     README.md
     references/buildout_thesis.md
     scripts/
       ai_buildout.py
       value_chain.py
       value_chain.yaml
   ```

2. Make sure `mover-screener` is installed alongside it:

   ```
   ~/.claude/skills/mover-screener/scripts/   (fmp_client, uw_client, ...)
   ```

3. Install the Python dependencies for the bundle:

   ```bash
   pip install pyyaml requests       # ai-buildout
   pip install finvizfinance          # mover-screener's universe scan
   ```

4. Set your API keys (next section), then restart Claude Code so it picks up the
   new skill. You can confirm it loaded by asking Claude to list skills, or just
   run the script directly to smoke-test.

---

## API keys

The script reads keys from environment variables. The simplest setup is to put
them in your shell profile (`~/.zshrc` or `~/.bashrc`) and `source` it:

```bash
export FMP_API_KEY="your_fmp_key"            # required
export UNUSUAL_WHALES_API_KEY="your_uw_key"  # optional (IV / gamma / flow)
export ALPACA_API_KEY="your_alpaca_key"      # optional (liquidity gate)
export ALPACA_API_SECRET="your_alpaca_secret"
```

```bash
source ~/.zshrc
```

SSL certificate handling is automatic (the engine imports `ssl_setup` from
mover-screener, which points requests at a valid cert bundle).

---

## Usage — the four modes

Run from the `scripts/` directory:

```bash
cd ~/.claude/skills/ai-buildout/scripts
source ~/.zshrc

python3 ai_buildout.py --mode bottleneck   # default: which layer is hot + movers
python3 ai_buildout.py --mode capex        # hyperscaler capex scoreboard (cheap)
python3 ai_buildout.py --mode power        # power / grid / cooling chain
python3 ai_buildout.py --mode thesis       # GREEN/YELLOW/RED thesis health
```

Inside Claude Code you can also just ask in plain language ("which part of the AI
chain is hot right now?", "is the AI buildout thesis still intact?") and Claude
will pick the mode, run it, and add the WebSearch overlay.

**Cost guide:** `capex` is the cheapest (about 7 API calls). `power` screens ~21
names. `bottleneck` and `thesis` screen all ~55 names (roughly 165–190 FMP calls,
about a minute or two with the built-in rate limiting).

---

## Flags reference

| Flag | Default | What it does |
|------|---------|--------------|
| `--mode` | `bottleneck` | `bottleneck`, `capex`, `power`, or `thesis` |
| `--top-layers N` | `2` | how many of the hottest layers to pull tradeable names from |
| `--flow-pages N` | `8` | UW market-flow pages for the smart-money scan |
| `--max-spread-pct` | `10.0` | options liquidity gate: max ATM bid/ask spread |
| `--no-flow` | off | skip the smart-money flow scan (faster) |
| `--no-liq` | off | skip the options liquidity gate (faster) |
| `--no-iv` | off | skip the IV-rank overlay (faster) |
| `--output-dir PATH` | `./reports/ai_buildout_<date>` (or `$CAMPAIGN_DIR/reports/...`) | where reports are saved |
| `--api-key KEY` | env var | override the FMP key |

Tip: for a fast structural run with no external overlays, combine
`--no-flow --no-liq --no-iv`.

---

## Example run-through

### Step 1 — check the money (capex)

```bash
python3 ai_buildout.py --mode capex
```

```
# AI-buildout — CAPEX scoreboard (leading indicator)

**Funding verdict: EXPANDING**  (7/7 funders raising capex YoY)

| Name  | Latest Q   | Capex  | QoQ  | YoY   | TTM    | Trend  |
|-------|------------|--------|------|-------|--------|--------|
| MSFT  | 2026-03-31 | $30.9B | +3%  | +84%  | $97.2B | RISING |
| GOOGL | 2026-03-31 | $35.7B | +28% | +107% | $109.9B| RISING |
| AMZN  | 2026-03-31 | $44.2B | +12% | +77%  | $151.0B| RISING |
| META  | 2026-03-31 | $19.0B | -11% | +47%  | $75.7B | RISING |
| ORCL  | 2026-02-28 | $18.6B | +55% | +218% | $48.2B | RISING |
| CRWV  | 2026-03-31 | $7.7B  | +90% | +447% | $16.6B | RISING |
| NBIS  | 2026-03-31 | $2.5B  | +20% | n/a   | $5.5B  | RISING |
```

Read: the demand engine is wide open. Capex rising across the board means the
spend will pull through down the chain. Worth hunting for which layer it's
hitting next.

### Step 2 — find the hot layer (bottleneck)

```bash
python3 ai_buildout.py --mode bottleneck
```

```
| Layer                  | Heat | n | S2 | S4 | avg r20 | avg coil |
|------------------------|------|---|----|----|---------|----------|
| HBM / memory           | 61.7 | 3 | 3  | 0  | +37%    | 8        |
| Networking / optical   | 55.7 | 8 | 8  | 0  | +12%    | 5        |
| Compute / accelerators | 55.6 | 5 | 5  | 0  | +11%    | 5        |
| ...                    |      |   |    |    |         |          |
| Power generation       | 19.4 | 9 | 2  | 5  | -3%     | 6        |

Binding / hottest layer: HBM / memory (heat 61.7)
```

Read: HBM is leading, with optical and compute right behind. Power has cooled off
(mostly Stage 4). The tradeable shortlist that follows is liquidity-gated, so
you only see names with real option markets.

### Step 3 — drill into power (power)

```bash
python3 ai_buildout.py --mode power
```

```
### Liquidity PASS (tradeable now)
  VRT   CALL $327.46  ATR6% pm13  r20  +1%  tgt +14%  IV50 liq:PASS
  GEV   CALL $1038.74 ATR4% pm12  r20 -10%  tgt +20%  IV38 liq:PASS
  MOD   CALL $260.52  ATR7% pm0   r20  +4%  tgt  n/a  IV97 liq:PASS
  ETN   CALL $391.35  ATR4% pm0   r20  -8%  tgt +20%  IV58 liq:PASS
```

Read: even with the power layer cool overall, four names clear the liquidity gate
with room to their analyst targets. VRT looks cleanest (mid IV, +14% to target,
coiling).

### Step 4 — sanity-check the thesis (thesis)

```bash
python3 ai_buildout.py --mode thesis
```

```
## Preliminary light: YELLOW

- Capex funding: OK   (verdict EXPANDING)
- Power chain heat: WEAK (33.0)
- Compute/HBM breadth: OK (58.7)

### Market context (sector ETF 20d return)
- SMH: +13.8%   XLK: +12.6%   XLE: +4.6%   SPY: +4.4%   XLU: -1.8%
```

Read: capex and chip-side breadth are healthy, but the power chain has pulled back
(confirmed by XLU utilities down 1.8%). Net YELLOW: the theme is intact, just
rotating. Claude then layers a WebSearch check of the thesis checkpoints and
writes the final call.

### Step 5 — Claude finishes the job

After the script runs, ask Claude to add the overlay: confirm the bottleneck with
current news, pull each PASS name's next-earnings date and catalyst, and turn the
best candidates into defined-risk option structures.

---

## What Claude adds at run time

The script gives you the measurable signals and a checklist. Claude then:

1. Confirms the binding layer against current news (the `bottleneck_tells`).
2. For capex, pulls the latest guidance language (raised / held / cut) and NVDA
   datacenter-revenue trend.
3. Pulls catalyst and next-earnings dates for each liquidity-PASS name (a 3-month
   expiry through a print is a binary, and Claude flags it).
4. Grades the thesis checkpoints in `references/buildout_thesis.md`.
5. Routes the winners into a defined-risk structure (IV-rank routed).

---

## Customizing the taxonomy

Open `scripts/value_chain.yaml` and edit freely. Each layer has `tickers`,
`bottleneck_tells`, and `notes`. The top of the file has `capex_proxies` (the
names whose capex you treat as the leading indicator) and `market_context` (the
sector ETFs used for the tailwind/headwind read). Bump the `version` field when
you change the map so it shows up in report headers.

Quick check after editing:

```bash
python3 value_chain.py
```

This prints the layer counts and the unique-ticker total so you can confirm your
edits parsed.

---

## Output files

Every run prints to the terminal and saves a markdown report plus a JSON payload
to the output directory (default `./reports/ai_buildout_<date>/`, or
`$CAMPAIGN_DIR/reports/...` if that env var is set):

```
ai_buildout_<mode>_<timestamp>.md     # the human-readable report
ai_buildout_<mode>_<timestamp>.json   # the structured data (for downstream use)
```

The JSON mirrors the layout used by mover-screener, so a staging pipeline can
pick it up.

---

## Companion skill: mover-screener

`mover-screener` is the market-wide engine that `ai-buildout` is built on. You
can run it entirely on its own. Where ai-buildout asks "which part of the AI
chain is hot," mover-screener asks "which high-beta names anywhere in the market
are set up to throw a big options move, in either direction."

### What it does

It systematically surfaces high-beta, big-ATR "secondary movers" across the whole
market. The target is a *style*, not a sector: high beta + wide ATR + liquid
monthly options. That style shows up in AI datacenter, optical, crypto-miners,
consumer tech, biotech, nuclear, energy, and more. It deliberately steers you
away from the crowded mega-caps (NVDA, GOOGL) toward the second- and
third-derivative names that actually move enough for options to pay.

Every candidate is bucketed by trend stage and pre-checked for tradeable option
liquidity:

- **Stage 2** (price > 50DMA > 200DMA, within 25% of the 52-week high) → **CALL**
  candidate.
- **Stage 4** (below the 200DMA, or more than 25% off the high) → **PUT**
  candidate.

### When to use it

- You want high-beta movers / big-swing options candidates (calls or puts).
- You want to stop defaulting to mega-caps and find the secondary movers.
- You want a market-wide, profile-based scan (not a single theme).
- You want both CALL and PUT candidates ranked, pre-checked for liquidity.

### Extra prerequisite

On top of the shared keys (FMP required; UW + Alpaca optional), mover-screener
needs one more Python package for its universe scan:

```bash
pip install finvizfinance
```

`finvizfinance` is free and needs no key. It supplies the candidate universe
(prefiltered by beta / average volume / price). An optional `FINVIZ_API_KEY`
unlocks the real-time "Elite" universe backend; without it, the skill silently
uses the free backend. (ai-buildout does **not** need finvizfinance, because it
screens a fixed taxonomy instead of scanning the market.)

### The pipeline

1. **Universe** — finvizfinance Technical screener (or Elite), prefiltered to
   beta / average volume / price. You can merge a hand-curated ticker list with
   `--universe-file` so those names are always screened.
2. **Profile filter** — per name, FMP pulls 260 days of history → ATR%(14),
   20/50/200-day moving averages, % from the 52-week high, 20d/60d returns, and
   beta. Keeps names with beta ≥ min AND ATR% ≥ min.
3. **Bucket** — Stage 2 → CALL, Stage 4 → PUT.
4. **Overlays** (shortlist only) — Unusual Whales squeeze signals (borrow fee /
   shares available / short-interest % of float) + IV rank, then the Alpaca
   monthly options liquidity gate on the side you'd actually trade.

It also has two "about to move" layers: a per-name **pre-move coil** signal
(volatility/range/volume contraction, always on) and a market-wide
**smart-money** discovery screen (`--smart-money`) that scans the unusual-flow
feed for aggressive sweeps + fresh volume/OI + dealer-gamma building before the
move shows up in price.

### Example commands

```bash
cd ~/.claude/skills/mover-screener/scripts
source ~/.zshrc

# Default: free finviz universe, beta>=1.5, ATR%>=5, both directions
python3 mover_screener.py --output-dir reports/

# Calls only (Stage 2 uptrends), tighter profile
python3 mover_screener.py --direction long --min-beta 2.0 --min-atr-pct 6 --output-dir reports/

# Puts only (Stage 4 breakdowns)
python3 mover_screener.py --direction short --output-dir reports/

# Add the market-wide smart-money pre-positioning screen
python3 mover_screener.py --smart-money --output-dir reports/

# Fast profile-only scan (skip UW + Alpaca overlays)
python3 mover_screener.py --no-overlays --output-dir reports/

# Standalone smart-money discovery, any time
python3 smart_money.py --pages 8 --min-premium 500000 --top 30 --output-dir reports/
```

### Key flags

| Flag | Default | Effect |
|------|---------|--------|
| `--min-beta` | `1.5` | minimum effective beta (max of profile / realized / finviz) |
| `--min-atr-pct` | `5.0` | minimum 14-day ATR% (swing-size floor) |
| `--direction` | `both` | `long` = CALL bucket only, `short` = PUT bucket only |
| `--universe-file` | — | merge curated tickers that are always screened |
| `--max-names` | `25` | max names reported per direction |
| `--scan-limit` | `300` | max universe candidates to pull histories for |
| `--max-spread-pct` | `10.0` | liquidity gate: max ATM bid/ask spread as % of mid |
| `--smart-money` | off | also run the market-wide smart-money flow + gamma screen |
| `--no-overlays` | off | skip UW + Alpaca (profile screen only, faster) |
| `--output-dir` | `reports/` | output directory |

### Output

`mover_screener_<timestamp>.json` and `.md` — ranked CALL and PUT tables with
beta, ATR%, trend, % from the 52-week high, IV rank, squeeze flags, liquidity
PASS/FAIL, and a one-line thesis per name. With `--smart-money`, you also get a
`smart_money_<timestamp>.md`.

---

## How the two skills fit together

| | mover-screener | ai-buildout |
|--|----------------|-------------|
| **Lens** | style (high beta, wide ATR) | theme (AI buildout value chain) |
| **Scope** | the whole market | a curated taxonomy of ~55 names |
| **Universe** | finvizfinance scan | fixed `value_chain.yaml` |
| **Organized by** | CALL / PUT buckets | value-chain layer + capex + thesis |
| **Shared engine** | owns the data clients | imports them |

A natural workflow: run `ai-buildout --mode capex` to confirm the buildout is
still funded, `--mode bottleneck` to see which layer is hot, then cross-check the
specific names with `mover-screener` (or run a full market-wide mover scan to
catch movers the AI taxonomy doesn't cover). Both feed the same end goal:
defined-risk, liquidity-gated options candidates.

---

## Guardrails and limits

- **It never places orders.** It surfaces candidates only.
- **Liquidity gate is mandatory** before any name is called "tradeable."
- Names trading **above** the analyst consensus target are flagged `EXTENDED`,
  not hidden.
- The thesis mode is **warn-only**. A RED read should down-weight the theme in
  your process; it does not auto-block trades.
- The layer-heat score is a **transparent heuristic** (0.35·stage2-tilt +
  0.25·momentum + 0.20·coil + 0.20·bull-flow). All components are reported so you
  can audit it. It's a ranking aid, not a prediction.
- Data is only as good as your FMP plan and the date you run it. Off-plan or
  illiquid symbols are skipped quietly and counted in the run summary.
- This is research tooling, not investment advice.

---

## Credits

The trade lens is informed by Leopold Aschenbrenner's "Situational Awareness"
(2024), read critically. The capex supercycle and rotating-bottleneck framing are
the useful parts. The AGI-date and intelligence-explosion timeline claims are the
weakest and most incentive-loaded, so the skill trades the capex trendline, not
the prophecy (see `references/buildout_thesis.md`).

Built on the `mover-screener` skill's data clients (FMP, Unusual Whales, Alpaca).
