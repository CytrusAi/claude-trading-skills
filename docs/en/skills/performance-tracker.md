---
layout: default
title: "Performance Tracker"
grand_parent: English
parent: Skill Guides
nav_order: 11
lang_peer: /ja/skills/performance-tracker/
permalink: /en/skills/performance-tracker/
generated: true
---

# Performance Tracker
{: .no_toc }

Turn a paper/live trading account and its thesis bookkeeping into quantstats performance tearsheets. Two views — an account-level tearsheet (Sharpe/Sortino, drawdown, monthly heatmap vs SPY) from the Alpaca equity curve, and a strategy-level trade ledger that measures return on DEPLOYED capital (per-trade R) instead of the cash-diluted account. Use when the user wants a performance report, a tearsheet, Sharpe/drawdown stats, account vs benchmark, return on risk, win rate, or to grade whether a trading edge is actually working.
{: .fs-6 .fw-300 }

<span class="badge badge-api">Alpaca Required</span>

[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/performance-tracker){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

# Performance Tracker - quantstats tearsheets + trade-level returns

---

## 2. When to Use

- User asks for a performance report, tearsheet, or Sharpe/drawdown stats
- User wants account return vs a benchmark (SPY)
- User asks "is my edge actually working" / win rate / return on risk
- User wants a backtest returns CSV scored with the same tooling as live
- Weekly or monthly campaign review

---

## 3. Prerequisites

- `ALPACA_API_KEY` / `ALPACA_API_SECRET` (paper account is fine);
  `ALPACA_BASE_URL` optional (defaults to the paper endpoint)
- Python deps: `quantstats`, `pandas`, `matplotlib`, `certifi` (and `pyyaml`
  for the trade ledger). Install: `pip install quantstats pandas matplotlib certifi pyyaml`
- Keys are read from the environment. Verified on Python 3.13 / pandas 2.2 /
  quantstats 0.0.81 (no compatibility patching needed).
- Paths resolve from `$CAMPAIGN_DIR` if set, else the current directory. Point
  `CAMPAIGN_DIR` at the campaign root that holds `state/theses/` and `reports/`.

---

## 4. Quick Start

- Pairs naturally with a weekly review: account tearsheet for the headline, trade
  ledger for whether the edge is real.
- A backtest and the live account share one scorer: feed the backtest's returns
  CSV through `perf_report.py --csv`, then compare Sharpe/drawdown against the
  live run. Backtest much better than live = edge leaking to slippage/fees.
- Educational / informational only. Not investment advice.

---

## 5. Workflow

- Pairs naturally with a weekly review: account tearsheet for the headline, trade
  ledger for whether the edge is real.
- A backtest and the live account share one scorer: feed the backtest's returns
  CSV through `perf_report.py --csv`, then compare Sharpe/drawdown against the
  live run. Backtest much better than live = edge leaking to slippage/fees.
- Educational / informational only. Not investment advice.

---

## 6. Resources

**Scripts:**

- `skills/performance-tracker/scripts/perf_report.py`
- `skills/performance-tracker/scripts/trade_ledger.py`
