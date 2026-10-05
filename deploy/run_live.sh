#!/bin/bash
# Launch the paper bot in the background with logging.
# Run any time — it waits for market hours on its own (trades 9:45-15:30 ET,
# flattens everything at 15:45 ET). Needs ALPACA_API_KEY/SECRET in env.
cd "$HOME/trade-algo-v2" || exit 1
git pull --quiet 2>/dev/null
export DATA_SOURCE=alpaca
export BROKER=alpaca
nohup "$HOME/trade-algo-v2/.venv/bin/python" "$HOME/trade-algo-v2/bot.py" \
  >> "$HOME/trade-algo-v2/live.log" 2>&1 &
echo "bot started (pid $!)."
echo "watch live:  tail -f ~/trade-algo-v2/live.log"
echo "status:      ~/trade-algo-v2/.venv/bin/python ~/trade-algo-v2/status.py"
echo "stop:        pkill -f trade-algo-v2/bot.py"
