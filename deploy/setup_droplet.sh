#!/bin/bash
# trade-algo-v2 droplet setup — paste into the DigitalOcean droplet console as root.
# Installs Python + deps (in a venv, per Ubuntu's PEP 668 rules), clones the repo,
# and (if Alpaca keys are exported) pulls 1-min bars and runs the backtest.
set -e

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git

APP="$HOME/trade-algo-v2"
if [ ! -d "$APP" ]; then
  git clone https://github.com/rus237sell/trade-algo-v2.git "$APP"
else
  git -C "$APP" pull --quiet
fi
cd "$APP"
if [ ! -d "$APP/.venv" ]; then
  python3 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" install --quiet -r requirements.txt
PY="$APP/.venv/bin/python"
echo "--- setup done ---"

if [ -n "$ALPACA_API_KEY" ] && [ -n "$ALPACA_API_SECRET" ]; then
  echo "Alpaca keys found. Pulling 1-min bars (free tier) and running backtest..."
  for s in QQQ SPY IWM; do
    DATA_SOURCE=alpaca "$PY" fetch_history.py $s 2026-09-01 2026-10-03
  done
  for s in QQQ SPY IWM; do
    echo "== $s =="
    "$PY" backtest.py data/${s}_1min.csv
  done
  echo "--- done. Copy the results above back to chat ---"
else
  echo "Alpaca keys not found. Run these first, then re-run this script:"
  echo "  export ALPACA_API_KEY=your-key-id"
  echo "  export ALPACA_API_SECRET=your-secret"
fi
