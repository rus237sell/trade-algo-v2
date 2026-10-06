"""Smoke test: prove the Alpaca paper fill path works end to end.

Buys 1 share of SPY on paper and immediately sells it, polling both
orders to fill. If this passes, the bot's broker path is proven --
any "bot isn't trading" issue is signals/session, not the broker.

Usage: ~/trade-algo-v2/.venv/bin/python smoke_test.py
Needs ALPACA_API_KEY / ALPACA_API_SECRET in env (same as the bot).
Run during market hours (before 1:00 PM PT) -- equity market orders
outside regular hours won't fill.
"""
import sys
import time

import requests

from alpaca import AlpacaBroker, PAPER_BASE


def wait_fill(broker, order_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{PAPER_BASE}/v2/orders/{order_id}",
                         headers=broker.auth.headers(), timeout=15)
        r.raise_for_status()
        o = r.json()
        if o.get("status") in ("filled", "partially_filled"):
            return o
        if o.get("status") in ("rejected", "canceled", "expired"):
            raise RuntimeError(f"order {o.get('status')}: {o}")
        time.sleep(1)
    raise RuntimeError("timed out waiting for fill")


def main():
    broker = AlpacaBroker()  # raises if keys missing
    tag = f"smoke-{int(time.time())}"

    print("BUY 1 SPY (paper)...")
    buy = broker._order("SPY", 1, "buy", tag + "-b")
    filled_buy = wait_fill(broker, buy["id"])
    print(f"  filled @ ${filled_buy.get('filled_avg_price')}")

    print("SELL 1 SPY (paper)...")
    sell = broker._order("SPY", 1, "sell", tag + "-s")
    filled_sell = wait_fill(broker, sell["id"])
    print(f"  filled @ ${filled_sell.get('filled_avg_price')}")

    print("SMOKE TEST PASSED: broker fill path works.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"SMOKE TEST FAILED: {e}")
        sys.exit(1)
