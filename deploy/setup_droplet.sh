#!/bin/bash
# trade-algo-v2 droplet setup — paste into the DigitalOcean droplet console as root.
# Installs Python + deps, clones the repo, and (if Alpaca keys are exported)
# pulls 1-min bars and runs the backtest on QQQ/SPY/IWM.
set -e

apt-get update -qq
apt-get install -y -qq python3 python3-pip git

if [ ! -d "$HOME/trade-algo-v2" ]; then
  git clone https://github.com/rus237sell/trade-algo-v2.git "$HOME/trade-algo-v2"
else
  git -C "$HOME/trade-algo-v2" pull --quiet
fi
cd "$HOME/trade-algo-v2"
pip3 install --quiet -r requirements.txt
echo "--- setup done ---"

if [ -n "$ALPACA_API_KEY" ] && [ -n "$ALPACA_API_SECRET" ]; then
  echo "Alpaca keys found. Pulling 1-min bars (free tier) and running backtest..."
  for s in QQQ SPY IWM; do
    DATA_SOURCE=alpaca python3 fetch_history.py $s 2026-09-01 2026-10-03
  done
  for s in QQQ SPY IWM; do
    echo "== $s =="
    python3 backtest.py data/${s}_1min.csv
  done
  echo "--- done. Copy the results above back to chat ---"
else
  echo "Alpaca keys not found. Run these first, then re-run this script:"
  echo "  export ALPACA_API_KEY=your-key-id"
  echo "  export ALPACA_API_SECRET=your-secret"
fi
