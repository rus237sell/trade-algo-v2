"""Idea 3: 5-minute ORB with wide target (Zarattini & Aziz).

Thesis: ORB works via fat right tail — many small losses, few large wins.
Tight brackets kill it. Use 10R target.

Rules:
- 9:30-9:35 candle: long if closed up, short if closed down. Skip dojis.
- Stop at opposite extreme of first candle.
- Target 10R. Exit at 15:59.

Usage: python strategy_orb.py data/QQQ_1min.csv
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
    dates = sorted(df["date"].unique())

    trades, wins, pnl = 0, 0, 0.0
    for date in dates:
        day = df[df["date"] == date].reset_index(drop=True)

        # First 5-min candle: 9:30-9:35
        orb = day[(day["time"].dt.time >= dtime(9, 30)) & (day["time"].dt.time < dtime(9, 35))]
        if len(orb) < 3:
            continue
        o, h, l, c = orb["open"].iloc[0], orb["high"].max(), orb["low"].min(), orb["close"].iloc[-1]
        if abs(c - o) < 0.01 * o:  # doji, skip
            continue

        direction = 1 if c > o else -1
        entry = c
        risk = (h - l)  # full candle range as R
        if risk <= 0:
            continue
        stop = l if direction == 1 else h
        target = entry + direction * 10 * risk

        # Walk forward from 9:35
        rest = day[day["time"].dt.time >= dtime(9, 35)].reset_index(drop=True)
        exit_px, exit_reason = None, "eod"
        for _, bar in rest.iterrows():
            if bar["time"].time() > dtime(15, 59):
                break
            if direction == 1:
                if bar["low"] <= stop:
                    exit_px, exit_reason = stop, "stop"
                    break
                if bar["high"] >= target:
                    exit_px, exit_reason = target, "target"
                    break
            else:
                if bar["high"] >= stop:
                    exit_px, exit_reason = stop, "stop"
                    break
                if bar["low"] <= target:
                    exit_px, exit_reason = target, "target"
                    break
        if exit_px is None:
            # EOD exit at 15:59
            eod = rest[rest["time"].dt.time <= dtime(15, 59)]
            if eod.empty:
                continue
            exit_px = eod["close"].iloc[-1]

        move = (exit_px - entry) * direction
        qty = max(1, int(313 / risk))  # $313 risk = 1R
        trade_pnl = move * qty
        trades += 1
        wins += trade_pnl > 0
        pnl += trade_pnl

    wr = wins / trades if trades else 0
    pf = "n/a"
    print(f"ORB: trades={trades} win_rate={wr:.1%} pnl=${pnl:,.2f}")
    return {"trades": trades, "win_rate": wr, "pnl": pnl}


if __name__ == "__main__":
    run(sys.argv[1])
