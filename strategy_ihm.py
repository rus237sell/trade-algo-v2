"""Idea 1: Last-half-hour intraday momentum (Gao et al. JFE 2018).

Thesis: SPY first half-hour return predicts last half-hour return,
driven by dealer hedging flows near the close.

Rules:
- At 10:00, r1 = price / prior close - 1
- At 15:30, r12 = return 15:00 -> 15:30
- Enter direction of r1 if sign(r1)==sign(r12) and |r1| > 20-day median |r1|
- Exit at 15:59. No stop (drift edge, not bracket).

Usage: python strategy_ihm.py data/QQQ_1min.csv
"""
import sys
from datetime import time as dtime

import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")


def load(csv_path):
    df = pd.read_csv(csv_path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("time").reset_index(drop=True)
    df["date"] = df["time"].dt.date
    return df


def run(csv_path):
    df = load(csv_path)
    # Need prior close: get last bar of each day
    daily_close = df.groupby("date")["close"].last()
    dates = sorted(df["date"].unique())
    # 20-day median of |r1| for filter
    r1_hist = []

    trades, wins, pnl = 0, 0, 0.0
    for i, date in enumerate(dates):
        if i == 0:
            continue  # need prior close
        day = df[df["date"] == date].reset_index(drop=True)
        prior_close = daily_close[dates[i - 1]]

        # r1: prior close -> 10:00
        b1000 = day[day["time"].dt.time <= dtime(10, 0)]
        if b1000.empty:
            continue
        p1000 = b1000["close"].iloc[-1]
        r1 = p1000 / prior_close - 1

        # r12: 15:00 -> 15:30
        b1500 = day[(day["time"].dt.time >= dtime(15, 0)) & (day["time"].dt.time <= dtime(15, 30))]
        if len(b1500) < 2:
            continue
        r12 = b1500["close"].iloc[-1] / b1500["close"].iloc[0] - 1

        # Filter: |r1| above 20-day median
        r1_hist.append(abs(r1))
        if len(r1_hist) < 20:
            continue
        median_r1 = sorted(r1_hist[-20:])[10]
        if abs(r1) <= median_r1:
            continue
        if (r1 > 0) != (r12 > 0):  # signs must agree
            continue

        # Enter at 15:30 in direction of r1
        entry = b1500["close"].iloc[-1]
        direction = 1 if r1 > 0 else -1

        # Exit at 15:59
        b1559 = day[day["time"].dt.time <= dtime(15, 59)]
        if b1559.empty:
            continue
        exit_px = b1559["close"].iloc[-1]

        move = (exit_px - entry) * direction
        # Size: $313 risk, but no stop — use 0.5% of entry as risk proxy
        qty = max(1, int(313 / (entry * 0.005)))
        trade_pnl = move * qty
        trades += 1
        wins += trade_pnl > 0
        pnl += trade_pnl

    wr = wins / trades if trades else 0
    print(f"IHM: trades={trades} win_rate={wr:.1%} pnl=${pnl:,.2f}")
    return {"trades": trades, "win_rate": wr, "pnl": pnl}


if __name__ == "__main__":
    run(sys.argv[1])
