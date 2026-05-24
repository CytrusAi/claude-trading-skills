---
name: performance-tracker
description: Turn a paper/live trading account and its thesis bookkeeping into quantstats performance tearsheets. Two views — an account-level tearsheet (Sharpe/Sortino, drawdown, monthly heatmap vs SPY) from the Alpaca equity curve, and a strategy-level trade ledger that measures return on DEPLOYED capital (per-trade R) instead of the cash-diluted account. Use when the user wants a performance report, a tearsheet, Sharpe/drawdown stats, account vs benchmark, return on risk, win rate, or to grade whether a trading edge is actually working.
---

# Performance Tracker - quantstats tearsheets + trade-level returns

Two complementary scorecards for a trading campaign:

1. **Account-level** (`perf_report.py`) - pulls the Alpaca portfolio equity curve,
   converts it to daily returns, benchmarks against SPY (same Alpaca data feed,
   no yfinance), and renders a full quantstats HTML tearsheet plus a text summary.
2. **Strategy-level** (`trade_ledger.py`) - parses your thesis YAML bookkeeping
   into a trade ledger, pulls live Alpaca option-leg P&L for the open book, and
   emits a closed-trade returns series (return on deployed capital). This is the
   honest read on the EDGE, because it strips out the idle cash that dilutes
   account-level stats.

## When to Use

- User asks for a performance report, tearsheet, or Sharpe/drawdown stats
- User wants account return vs a benchmark (SPY)
- User asks "is my edge actually working" / win rate / return on risk
- User wants a backtest returns CSV scored with the same tooling as live
- Weekly or monthly campaign review

## Prerequisites

- `ALPACA_API_KEY` / `ALPACA_API_SECRET` (paper account is fine);
  `ALPACA_BASE_URL` optional (defaults to the paper endpoint)
- Python deps: `quantstats`, `pandas`, `matplotlib`, `certifi` (and `pyyaml`
  for the trade ledger). Install: `pip install quantstats pandas matplotlib certifi pyyaml`
- Keys are read from the environment. Verified on Python 3.13 / pandas 2.2 /
  quantstats 0.0.81 (no compatibility patching needed).
- Paths resolve from `$CAMPAIGN_DIR` if set, else the current directory. Point
  `CAMPAIGN_DIR` at the campaign root that holds `state/theses/` and `reports/`.

## Account-level tearsheet

```bash
# last 3 months vs SPY, opens in a browser when interactive
python3 scripts/perf_report.py --period 3M

python3 scripts/perf_report.py --period all --no-open      # whole history, headless
python3 scripts/perf_report.py --csv returns.csv --title "Backtest"   # score a CSV
python3 scripts/perf_report.py --no-benchmark              # skip SPY
```

- `--period` accepts `1M | 3M | 6M | 1A | all` (ignored with `--csv`).
- `--csv` takes a file whose first column is a date plus a `returns` (decimal)
  or `equity` column - the same machinery scores a backtest or a live account.
- Output: `reports/perf_<period>_<ts>.html` + a text summary; appends to
  `reports/_run_log.txt`.
- **Caveat:** account-level returns are cash-diluted. With a mostly-cash account
  the vol/drawdown read small and Sharpe is noisy. For the edge, use the ledger.

## Strategy-level trade ledger

```bash
python3 scripts/trade_ledger.py                 # ledger + live open-book P&L
python3 scripts/trade_ledger.py --report        # chain into a tearsheet (>=2 closed)
python3 scripts/trade_ledger.py --no-positions  # offline (skip Alpaca)
```

- Reads `state/theses/*.yaml`, pulls Alpaca option legs (matched to theses by
  underlying), and writes `reports/trade_ledger_<ts>.csv` plus
  `reports/trade_returns_<ts>.csv` (`date,returns` where
  `returns = realized_pnl / capital_at_risk`).
- `return_on_risk` is an R measure: `+0.5` = made half of what you risked.
- With `--report` and >=2 closed trades, it calls `perf_report.py --csv` to
  render a tearsheet of the STRATEGY (not the account).

### Recording an exit (so closed trades enter the returns series)

When a position closes, set `status: CLOSED` in its thesis and add an `exit:`
block:

```yaml
status: CLOSED
exit:
  exit_date: 2026-05-26
  realized_pnl: -250.0        # net $ across ALL spreads (+ = gain)
  capital_at_risk: 930.0      # optional; else position_target.max_loss
  note: "Closed combo at $6.80 credit vs $9.30 debit"
```

Until that block exists a trade counts as OPEN (unrealized via Alpaca) and is
excluded from the realized returns series.

## Workflow notes

- Pairs naturally with a weekly review: account tearsheet for the headline, trade
  ledger for whether the edge is real.
- A backtest and the live account share one scorer: feed the backtest's returns
  CSV through `perf_report.py --csv`, then compare Sharpe/drawdown against the
  live run. Backtest much better than live = edge leaking to slippage/fees.
- Educational / informational only. Not investment advice.
