# Trade-algo-v2 roadmap

Paper-trading R&D. Nothing here touches real money.

## Efficiency-ratio regime filter (from his video find, 10/8/2026)

**Idea (his words):** don't predict direction — predict *chop*. The video's
creator trains a logistic regression on ~15 microstructure measurements to
forecast the next day's Efficiency Ratio for NQ futures, then skips trades on
predicted low-efficiency (choppy) days. Reported backtest: win rate 61.6%,
profit factor 1.481, expectancy $377/trade (vs $141.7k → $255.7k with the
filter). Backtested/hypothetical, not advice.

**What ER is (Kaufman):** `ER = |close - close[N]| / Σ|close[i] - close[i-1]|`,
0–1. Near 1 = clean trend, near 0 = chop. Also the basis of KAMA.

**How it maps to this bot** (1-min 0DTE scalper on QQQ/SPY/IWM — not NQ
daily, so expect smaller gains than the video's; a filter improves a strategy,
it doesn't create edge from nothing):

- **Stage 1 — trailing ER gate (no ML).** Compute 30-bar Kaufman ER on the
  underlying's 1-min bars; skip *entries* when ER < 0.30 (chop). Exits
  unaffected. Backtest the gate on the existing CSVs via backtest.py:
  report win rate / profit factor / expectancy with and without.
- **Stage 2 — ML regime prediction (the video's approach, adapted).**
  Logistic regression on ~15 intraday features (realized vol, ER lags,
  range expansion, volume z-score, time-of-day, etc.) to predict next-hour
  ER regime (high/med/low); gate entries on predicted low. Train on the
  collected paper-trade logs + bar CSVs. Keep it to sklearn, <100 lines.
- **Guardrails:** gate affects entries only, never the Treasurer's hard
  stops (+$500 sweep / −$1,000 halt / 12:45 PT flatten). Any gate change
  must beat the no-gate backtest on expectancy before it ships to paper.

## Done
- 10/8: fixed ET/UTC bug in alpaca.py::bars_1min (was shifting every request
  window −4h, starving the feed).
