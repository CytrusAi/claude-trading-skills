---
name: mover-screener
description: Hunt high-beta secondary "mover" stocks market-wide for an options-trading campaign. Surfaces the second- and third-derivative names (APLD, IREN, NBIS, LITE, DXCM, SPOT type) that throw big directional swings for BOTH calls and puts, not the crowded mega-caps. Use when the user wants high-beta movers, big-ATR options candidates, secondary momentum names, call/put swing candidates across sectors, or a profile-based (not theme-based) market scan with a liquidity gate. Buckets Stage 2 uptrends as CALL candidates and Stage 4 breakdowns as PUT candidates.
---

# Mover Screener — High-Beta Secondary Movers

Systematically surface high-beta, big-ATR secondary movers across the WHOLE
market for a directional options campaign. The target is a STYLE (high beta +
wide ATR + liquid monthly options), not a sector, so it spans AI datacenter,
optical, crypto-miners, consumer-tech, biotech, nuclear, energy, and more.

Stage 2 uptrends become CALL candidates; Stage 4 breakdowns become PUT
candidates. Every shortlisted name passes a mandatory options liquidity gate.

## When to Use

- User wants high-beta movers / big swing candidates for options (calls or puts)
- User wants to STOP defaulting to mega-caps (NVDA, GOOGL) and find the
  secondary movers (APLD, IREN, NBIS, LITE, DXCM, SPOT type)
- User asks for a market-wide, profile-based scan (not a single theme)
- User wants both CALL (uptrend) and PUT (breakdown) candidates ranked
- User wants candidates pre-checked for tradeable option liquidity

## Prerequisites

- `finvizfinance` (free, already installed) — the default universe source
- `FMP_API_KEY` — per-name technicals (uses ONLY `/stable/` endpoints)
- `UNUSUAL_WHALES_API_KEY` (optional) — squeeze + IV overlays
- `ALPACA_API_KEY` / `ALPACA_API_SECRET` (optional) — options liquidity gate
- `FINVIZ_API_KEY` (optional, future) — enables the real-time Elite universe
  backend. Absent → silently uses the free finvizfinance backend.

All keys are read from the environment. SSL is handled via certifi
(`scripts/ssl_setup.py` sets `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` before
finvizfinance loads — avoids macOS `CERTIFICATE_VERIFY_FAILED`).

## Workflow

### Step 1: Run the screener

```bash
# Default: free finviz universe, beta>=1.5, ATR%>=5, both directions
python3 ~/.claude/skills/mover-screener/scripts/mover_screener.py \
  --output-dir reports/

# Merge the hand-curated Tier-2 universe so those names are always screened
python3 ~/.claude/skills/mover-screener/scripts/mover_screener.py \
  --universe-file ~/Desktop/trading_campaign/UNIVERSE.md \
  --output-dir reports/

# Calls only (Stage 2 uptrends), tighter profile
python3 ~/.claude/skills/mover-screener/scripts/mover_screener.py \
  --direction long --min-beta 2.0 --min-atr-pct 6 --output-dir reports/

# Puts only (Stage 4 breakdowns)
python3 ~/.claude/skills/mover-screener/scripts/mover_screener.py \
  --direction short --output-dir reports/

# FINVIZ Elite (real-time, no row cap) — only if FINVIZ_API_KEY is set,
# otherwise falls back to free automatically
python3 ~/.claude/skills/mover-screener/scripts/mover_screener.py \
  --universe-source finviz_elite --output-dir reports/

# Fast profile-only scan (skip UW + Alpaca overlays)
python3 ~/.claude/skills/mover-screener/scripts/mover_screener.py \
  --no-overlays --output-dir reports/
```

### CLI options

| Flag | Default | Effect |
|------|---------|--------|
| `--universe-source` | `finviz_free` | `finviz_free` (no key) or `finviz_elite` (needs `FINVIZ_API_KEY`, else falls back) |
| `--min-beta` | `1.5` | Minimum effective beta (max of profile / realized / FINVIZ) |
| `--min-atr-pct` | `5.0` | Minimum 14-day ATR% (swing-size floor) |
| `--direction` | `both` | `long` = CALL bucket only, `short` = PUT bucket only, `both` |
| `--universe-file` | — | Merge curated tickers (e.g. UNIVERSE.md) — always screened |
| `--max-names` | `25` | Max names reported PER direction |
| `--scan-limit` | `300` | Max universe candidates to pull histories for (ranked by beta) |
| `--max-spread-pct` | `10.0` | Liquidity gate: max ATM bid/ask spread as % of mid |
| `--no-overlays` | off | Skip UW + Alpaca (profile screen only, faster) |
| `--smart-money` | off | Also run the market-wide smart-money pre-positioning discovery screen (UW flow + dealer gamma); writes `smart_money_*.md` and annotates the shortlist with matching flow lean |
| `--api-key` | env | FMP key override |
| `--output-dir` | `reports/` | Output directory |

### "About to move" signals (pre-move detection)

Two anticipatory layers find names *before* the move, not just names already moving:

1. **Pre-move coil (always on, per name).** Phase 2 computes a volatility-contraction
   signal: ATR(10)/ATR(50), 10d/50d range tightness, and 10d/50d volume dry-up. When
   ≥2 contract, the name is flagged **COILED pre-move (score)** in its thesis — a coiled
   spring primed to release (Stage 2 coil → CALL break, Stage 4 coil → PUT break).
2. **Smart-money pre-positioning (`--smart-money`, market-wide discovery).** Scans the UW
   unusual-flow feed (not a known ticker) to find where aggressive sweeps + fresh
   volume/OI + dealer-gamma-building point to positioning *before* the move. Ranks by
   conviction, assigns direction (call vs put premium dominance), and runs the top names
   through the FMP profile + Alpaca liquidity gate. Output: `smart_money_*.md`.

Run the standalone smart-money screen any time:
```bash
python3 ~/.claude/skills/mover-screener/scripts/smart_money.py \
  --pages 8 --min-premium 500000 --top 30 --output-dir reports/
```

### Step 2: Pipeline (what runs)

1. **Universe** — finvizfinance Technical screener (free) or Elite, prefiltered
   to Beta / Avg Volume / Price; curated `--universe-file` names merged in.
2. **Profile filter** — per name, FMP 260d history → ATR%(14), 20/50/200 DMA,
   % from 52w high, 20d/60d returns; beta from `/stable/profile` (with
   realized-beta and FINVIZ-beta fallbacks). Keep beta≥min AND ATR%≥min.
3. **Bucket** — Stage 2 (price>50DMA>200DMA, within 25% of 52w high) → CALL;
   Stage 4 (below 200DMA or >25% below 52w high) → PUT.
4. **Overlays** — UW squeeze (borrow fee / shares available / SI%float) + IV
   rank; Alpaca monthly liquidity gate on the side we'd trade. Shortlist only.

### Step 3: Review and present

Read the generated JSON + Markdown. Present two ranked tables:

- **CALL candidates** (Stage 2): beta, ATR%, trend, %from-52wH, IV rank,
  squeeze flags, liquidity PASS/FAIL, one-line thesis.
- **PUT candidates** (Stage 4): same columns.

Lead with names that pass the liquidity gate. For FAILs, note "use a
defined-risk spread or skip". Flag extended names (+40-60%/60d) as
pullback-preferred. Remind: verify earnings dates vs expiry, size down for
wider stops (high ATR → wide 2-ATR stops). Load
`references/mover_screening_methodology.md` for interpretation.

## Output

- `mover_screener_YYYY-MM-DD_HHMMSS.json` — structured results + metadata
- `mover_screener_YYYY-MM-DD_HHMMSS.md` — ranked CALL / PUT tables + detail

The universe backend actually used is printed at the end of the run and stored
in the report metadata.

## Notes & gotchas

- FMP `/api/v3/` endpoints are retired (403); this skill uses only `/stable/`.
- `/stable/quote` takes one symbol per call (comma-batching returns []).
- UW `shorts/{ticker}/interest-float` is often empty for newer names — handled.
- Borrow `fee_rate` is in PERCENT (0.35 = 0.35%, not 35%).
- Weeklies are illiquid for secondary names — the gate prices monthlies only.
- Educational / informational only. Not investment advice.

## Resources

- `references/mover_screening_methodology.md` — beta/ATR proxies, Stage 2 vs 4
  bucketing, squeeze mechanics, the liquidity gate (AMZN lesson), monthly-vs-
  weekly liquidity, and "How to enable FINVIZ Elite later".
- `scripts/universe_source.py` — finviz_free + finviz_elite backends
- `scripts/fmp_client.py` — FMP `/stable/` profile + history
- `scripts/uw_client.py` — squeeze + IV overlays
- `scripts/alpaca_client.py` — monthly options liquidity gate
- `scripts/indicators.py` — ATR%, DMAs, returns, stage classification
- `scripts/report_generator.py` — JSON + Markdown dual output
