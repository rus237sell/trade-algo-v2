"""Fetch free 1-min bars into CSVs for backtesting.

Free sources (no subscription):
  DATA_SOURCE=alpaca  -> Alpaca free tier (15-min delayed + history).
                        Needs ALPACA_API_KEY / ALPACA_API_SECRET (paper keys).
  DATA_SOURCE=tradier  -> Tradier (needs subscription as of Oct 2026).

Usage:
    export ALPACA_API_KEY=... ALPACA_API_SECRET=...
    DATA_SOURCE=alpaca python fetch_history.py QQQ 2026-09-01 2026-10-03
"""
import csv
import os
import sys
from datetime import datetime, timedelta

from data import TradierData
from alpaca import AlpacaData


def get_data():
    source = os.getenv("DATA_SOURCE", "alpaca")
    if source == "alpaca":
        d = AlpacaData()
        if d.auth.dry:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_API_SECRET "
                             "(free paper keys from alpaca.markets)")
        return d
    d = TradierData(env="sandbox")
    if d.dry:
        raise SystemExit("Set TRADIER_API_KEY (requires subscription)")
    return d


def fetch(symbol, start, end, out_path):
    data = get_data()
    rows = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # weekdays only
            day_end = cur + timedelta(days=1)
            bars = data.bars_1min(symbol, cur, min(day_end, end + timedelta(days=1)))
            rows.extend(bars)
            print(f"{cur.date()}: {len(bars)} bars")
        cur += timedelta(days=1)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        w.writeheader()
        for b in rows:
            w.writerow({k: b.get(k) for k in
                        ["time", "open", "high", "low", "close", "volume"]})
    print(f"wrote {len(rows)} bars -> {out_path}")


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
    start = datetime.strptime(sys.argv[2], "%Y-%m-%d") if len(sys.argv) > 2 else \
        datetime.now() - timedelta(days=30)
    end = datetime.strptime(sys.argv[3], "%Y-%m-%d") if len(sys.argv) > 3 else \
        datetime.now()
    os.makedirs("data", exist_ok=True)
    fetch(symbol, start, end, f"data/{symbol}_1min.csv")
