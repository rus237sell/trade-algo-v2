"""Live status: bot state + Alpaca paper account summary.

Usage: ~/trade-algo-v2/.venv/bin/python ~/trade-algo-v2/status.py
Paste the output in chat for a human-readable read.
"""
import json
import os

import requests

from alpaca import AlpacaAuth

BASE = os.path.dirname(os.path.abspath(__file__))
PAPER = "https://paper-api.alpaca.markets"

print("== BOT ==")
try:
    with open(os.path.join(BASE, "state.json")) as f:
        st = json.load(f)
    print(f"as of {st['time']}")
    print(f"session={st['session']} | day_pnl={st['day_pnl']:+.2f} | "
          f"halted={st['halted']} | data_lag={st['data_lag_min']} min")
    for p in st["positions"]:
        print(f"  OPEN {p['symbol']} {p['side']} {p['qty']}x {p['occ']}")
    if not st["positions"]:
        print("  no open bot positions")
except FileNotFoundError:
    print("bot has not written state yet (not running?)")

print("== ALPACA PAPER ==")
auth = AlpacaAuth()
if auth.dry:
    print("no keys in this shell's env")
else:
    h = auth.headers()
    acct = requests.get(f"{PAPER}/v2/account", headers=h, timeout=15).json()
    print(f"equity=${acct.get('equity')} buying_power=${acct.get('buying_power')}")
    poss = requests.get(f"{PAPER}/v2/positions", headers=h, timeout=15).json()
    for p in poss:
        print(f"  {p['symbol']} {p['qty']} uPL={float(p['unrealized_pl']):+.2f}")
    if not poss:
        print("  no open positions")
    print("-- recent orders --")
    orders = requests.get(f"{PAPER}/v2/orders", headers=h, timeout=15,
                          params={"status": "closed", "limit": 10,
                                  "direction": "desc"}).json()
    for o in (orders or [])[:10]:
        print(f"  {str(o.get('created_at'))[:16]} {o.get('symbol')} "
              f"{o.get('side')} {o.get('qty')} {o.get('status')} "
              f"fill={o.get('filled_avg_price')}")
