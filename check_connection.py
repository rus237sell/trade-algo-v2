"""Verify the Alpaca paper connection.

Usage: ~/trade-algo-v2/.venv/bin/python check_connection.py
"""
import requests

from alpaca import AlpacaAuth

PAPER_BASE = "https://paper-api.alpaca.markets"

auth = AlpacaAuth()
if auth.dry:
    print("FAIL: ALPACA_API_KEY / ALPACA_API_SECRET are not set in this shell.")
    print("Run the two export lines first, then re-run this script.")
    raise SystemExit(1)

r = requests.get(f"{PAPER_BASE}/v2/account", headers=auth.headers(), timeout=15)
if r.status_code == 401:
    print("FAIL: Alpaca rejected the keys (401).")
    print("Regenerate paper keys at alpaca.markets and re-export them.")
    raise SystemExit(1)
r.raise_for_status()
acct = r.json()
print(f"OK: connected to paper account {acct.get('account_number')}")
print(f"  equity=${acct.get('equity')}  buying_power=${acct.get('buying_power')}")
print(f"  status={acct.get('status')}  options_level={acct.get('options_trading_level')}")
