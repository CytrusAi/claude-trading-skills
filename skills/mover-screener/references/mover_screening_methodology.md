# Mover Screening Methodology

How the mover screener finds high-beta secondary "mover" stocks for a
directional options campaign, and why each gate exists.

## The core idea: a STYLE, not a sector

The target is a *profile*, not a theme. We want the second- and third-derivative
names that throw big percentage swings (good for calls AND puts), not the
crowded mega-caps. That profile shows up everywhere: AI datacenter (APLD, IREN,
NBIS), optical (LITE, COHR), crypto-miners (CIFR, WULF), consumer-tech (SPOT,
RDDT, HOOD), medtech (DXCM, PODD), nuclear (OKLO, SMR), energy services (LBRT,
RIG). Discovery is therefore market-wide and profile-based, never theme-limited.

## Beta and ATR% as swing proxies

Two numbers define a "mover":

- **Beta** — how hard the name moves relative to the market (systematic swing).
  Beta 1.5 means it tends to move 1.5x the index; APLD-type names run 4-6.
- **ATR% (14-day)** — average true range as a percent of price (absolute daily
  swing). ATR% 5% means a typical day spans 5% of the price; movers run 8-15%.

High on both = bigger directional option payoffs in either direction. ATR% also
sets stop width: a 9% ATR name needs a ~18% (2-ATR) stop, so position size DOWN
to keep dollar risk inside the per-trade cap.

**Effective beta** = max(FMP `/stable/profile` beta, realized beta vs SPY,
FINVIZ beta). Vendor beta is often null for recent listings (IREN, NBIS) or
lags fast-moving names, so we never let a single stale number exclude a
genuinely swingy stock.

## Stage 2 vs Stage 4 bucketing (direction)

Weinstein-style staging picks the option side:

- **Stage 2 (uptrend) → CALL side.** price > 50DMA > 200DMA AND within 25% of
  the 52-week high. Clean uptrend → long call / debit-call-spread / bull-put.
- **Stage 4 (downtrend) → PUT side.** below the 200DMA OR more than 25% below
  the 52-week high. Breakdown → long put / debit-put-spread / bear-call.
- **Neutral** (chop / base) is reported context but not actionable.

High beta cuts both ways: in risk-off regimes the same volatility that powers
breakout calls powers breakdown puts.

## Squeeze overlay (Unusual Whales)

Short-squeeze pressure amplifies upside swings on a catalyst. We read three
signals from `shorts/{ticker}/data` and `shorts/{ticker}/interest-float`:

- **Borrow fee rate** (annualized, in PERCENT). General-collateral / easy-to-
  borrow names sit near ~0.3-0.5%. 5%+ means real borrow pressure; 20%+ is hot.
  (Verified: fee_rate + rebate_rate ≈ the broker base rate, so APLD's "0.3475"
  is 0.35%, not 35%.)
- **Short shares available** — a thin borrow pool (≤ ~500k) means new shorts
  struggle to size, so a squeeze unwinds violently.
- **Short interest % of float** — ≥ 15% is a crowded short. Often empty
  (`{"data": []}`) for newer names; absence is handled gracefully, never fatal.

Two or more flags → "high" squeeze; one → "moderate"; none → "low".

## IV overlay (Unusual Whales iv-rank)

`stock/{ticker}/iv-rank` gives the 1-year IV rank (0-100):

- **< 30 (low)** — options are cheap relative to their own history → buy-vol /
  long-option bias (long calls/puts capture the move cheaply).
- **> 60 (high)** — options are rich → sell-vol / spread bias (debit/credit
  spreads, sell premium against the move to cut theta and vega risk).
- 30-60 — either structure works.

## The liquidity gate (mandatory — the AMZN lesson)

A great setup with untradeable options is a non-starter. We got burned by AMZN,
a *mega-cap*, whose options wouldn't fill at a usable spread; secondary names
can be worse. So before any name reaches the actionable shortlist:

- Price the **nearest standard MONTHLY expiry** (3rd-Friday OCC), ~30-60 DTE.
- Look at the **near-the-money** contract on the side we'd trade (call for
  Stage 2, put for Stage 4) — far OTM/ITM strikes are wide even on liquid names.
- Require a **live two-sided quote** (bid > 0 AND ask > 0).
- Require **bid/ask spread ≤ 10% of mid**.
- FAIL → flagged "ILLIQUID — spread only or skip"; use a defined-risk spread
  (tighter fills) or pass on the name.

### Monthly vs weekly liquidity

Weeklies are deliberately ignored. For secondary movers, weekly chains are thin
and one-sided; the monthly (3rd-Friday) expiry concentrates open interest and
two-sided quotes. We only price monthlies. (The Alpaca snapshot feed does not
return open interest, so the gate keys on live spread + two-sided quote, which
in practice separates liquid ~2-7% chains from thin 35-50% ones.)

## Data sources (free-first, Elite-optional)

- **Universe:** finvizfinance (free, ~15-min delayed) Technical screener,
  prefiltered to Beta / Average Volume / Price. FINVIZ also returns ATR, SMA
  (relative), and 52W-High distance, used as a fast prefilter and FMP fallback.
- **Per-name technicals:** FMP `/stable/` only — `/stable/historical-price-eod/
  full` for OHLCV (ATR%, DMAs, returns, 52w) and `/stable/profile` for beta.
  The `/api/v3/` family was retired Aug 31 2025 (403 "Legacy Endpoint") and is
  never used.
- **Squeeze + IV:** Unusual Whales.
- **Liquidity:** Alpaca options snapshots.
- **Curated names:** `--universe-file` merges hand-picked tickers (e.g.
  `trading_campaign/UNIVERSE.md` Tier-2 lists) so they are always screened.

## How to enable FINVIZ Elite later

The Elite path is stubbed-but-wired. It activates purely by setting an env var —
no code changes:

1. Subscribe to FINVIZ Elite and get your auth token.
2. Add it to your shell: `export FINVIZ_API_KEY="your_token"` (e.g. in
   `~/.zshrc`), then `source ~/.zshrc`.
3. Run with `--universe-source finviz_elite`.

What you gain: real-time data (vs ~15-min delay), no row cap, and a float
column for tighter squeeze reads. If `FINVIZ_API_KEY` is absent — or the Elite
request fails — the screener silently falls back to the free finvizfinance
backend, so the command never breaks. The backend actually used is printed at
the end of every run and recorded in the report metadata.

## Trade-offs to manage (from UNIVERSE.md)

1. **Liquidity gate** — non-negotiable (above).
2. **Wider stops** — 9% ATR ⇒ ~18% 2-ATR stop; size down to fit the 5-8%
   per-trade dollar-risk cap.
3. **Earnings landmines** — these move 10-20% on prints. Verify the earnings
   date is outside the option expiry (or structure around it) before entry.
4. **Gap risk** — high-beta names gap hard on news; prefer defined-risk spreads
   when IV rank > 50.
5. **Avoid parabolic chasing** — extended names (+40-60%/60d) favor pullback
   entries or premium-selling structures over chasing breakouts.
