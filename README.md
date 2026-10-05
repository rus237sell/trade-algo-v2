# trade-algo-v2

## Goal
Paper-trade the Scalp City 0DTE scalping system: 1-min VWAP / 50-EMA / opening-range-breakout signals on QQQ, SPY, IWM, with ATR exits and hard daily risk guardrails. Max 5 concurrent "worker" positions.

## Macro steps
1. Pull 1-min bars per symbol during market hours (Tradier, sandbox = paper).
2. On each closed bar check three triggers: candle close across VWAP, close across the 50 EMA, close outside the first-15-min range (up = call, down = put).
3. Filters: skip VWAP crosses when 9/21 EMAs are squeezed together; cooldown between trades per symbol.
4. Buy 0DTE ATM call/put; size so a 1x ATR adverse move risks ~$313.
5. Exit at 1.5x ATR target or 1.0x ATR stop; halt for the day at +$500 or -$1,000; force-flat everything at 15:45 ET.

## Free tier (no paid data)
- `python fetch_history.py QQQ 2026-09-01 2026-10-03` pulls free 1-min bars from the Tradier sandbox (free signup, delayed + historical data included).
- `python backtest.py data/QQQ_1min.csv` replays the strategy: win rate, P&L.
- `python bot.py` runs in dry-run mode with no keys, logging what it would trade.
- Honest limit: sandbox data is ~15-min delayed, so live 1-min scalping needs paid real-time data. Validate free, pay only if the backtest earns it.

## Versions
- v2.1: free-tier loop - fetch_history.py + backtest on real 1-min bars, no paid data needed.
- v2.0: initial scaffold. Dry-run mode, Tradier sandbox adapter, backtest harness.

## Short results
- Paper only. No live trades yet. Backtest pending historical 1-min data.
