# trade-algo-v2

## Goal
Paper-trade the Scalp City 0DTE scalping system: 1-min VWAP / 50-EMA / opening-range-breakout signals on QQQ, SPY, IWM, with ATR exits and hard daily risk guardrails. Max 5 concurrent "worker" positions.

## Macro steps
1. Pull 1-min bars per symbol during market hours (Tradier, sandbox = paper).
2. On each closed bar check three triggers: candle close across VWAP, close across the 50 EMA, close outside the first-15-min range (up = call, down = put).
3. Filters: skip VWAP crosses when 9/21 EMAs are squeezed together; cooldown between trades per symbol.
4. Buy 0DTE ATM call/put; size so a 1x ATR adverse move risks ~$313.
5. Exit at 1.5x ATR target or 1.0x ATR stop; halt for the day at +$500 or -$1,000; force-flat everything at 15:45 ET.

## Free tier (no subscription)
- Alpaca paper trading is free: signup at alpaca.markets, paper API keys, no subscription. Paper accounts support options.
- `DATA_SOURCE=alpaca BROKER=alpaca python bot.py` paper-trades for free (fill the two env vars first).
- `DATA_SOURCE=alpaca python fetch_history.py QQQ 2026-09-01 2026-10-03` pulls free 1-min bars (15-min delayed + full history), then `python backtest.py data/QQQ_1min.csv` replays the strategy.
- Honest limit: free data is ~15-min delayed, so live 1-min scalping needs paid real-time data. Validate free, pay only if the backtest earns it.
- Tradier moved behind a subscription (Oct 2026) — adapter kept for later, not the free path.

## Live paper trading
- On the droplet: `bash ~/trade-algo-v2/deploy/run_live.sh` (needs ALPACA_API_KEY/SECRET exported; pulls latest code, runs in background, logs to live.log).
- Watch: `tail -f ~/trade-algo-v2/live.log`
- Status anytime: `~/trade-algo-v2/.venv/bin/python ~/trade-algo-v2/status.py` — bot state + Alpaca paper equity/positions/recent fills. Paste it in chat for a read.
- Stop: `pkill -f trade-algo-v2/bot.py`
- The bot warns in the log if market data is >5 min stale (free tier may be delayed — paper results then prove plumbing, not edge).
- Fail-safe: if the freshest 1-min bar is older than 30 min (free tier runs ~15-min delayed), new entries are blocked until data recovers; exits and the 15:45 ET flatten still run. `stale_data` in state.json shows it.
- Restart-safe: on startup the bot re-adopts open 0DTE positions from the broker (same-day expiry, strategy symbols only), so a crash or reboot can't orphan positions — ATR exits and the flatten still fire for them. Recovered legs carry a `recovered` flag in state.json.

## Versions
- v2.6: startup position recovery — re-adopt today's open 0DTE legs from the broker at boot (ATR exits + EOD flatten cover them; entry unknown, measured from recovery price); TradierBroker.positions() added; broker outages during recovery never stop the bot.
- v2.5: live paper monitoring — state.json heartbeat, status.py, run_live.sh launcher, data-staleness warning.
- v2.4: backtest correctness fixes — regular-session bars only, VWAP/ORB reset daily, cooldown enforced between trades (the first run's -$116k was backtest bugs: extended-hours bars + cumulative VWAP + no cooldown, not the strategy).
- v2.3: one-command droplet setup script; Alpaca connection checker.
- v2.2: free Alpaca paper trading path (Tradier went paid).

## Short results
- Paper only. No live trades yet. Backtest pending historical 1-min data.
