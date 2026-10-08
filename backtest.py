"""Backtest the v2 signals on a CSV of 1-min bars.

CSV columns: time,open,high,low,close,volume
Regular session (9:30-16:00 ET) only; VWAP/ORB computed per day.
Fills simulated at the signal bar close; exits on ATR target/stop or EOD.
Slippage: flat $1/contract/side. One position at a time; cooldown between
trades per spec.

Usage: python backtest.py bars.csv
"""
import sys
from datetime import time as dtime, timedelta

import pandas as pd
import pytz

from config import Config
from signals import check_signals, atr
from risk import contracts_for_risk, DailyGuard

ET = pytz.timezone("America/New_York")


def load_session(csv_path):
    df = pd.read_csv(csv_path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("time").reset_index(drop=True)
    tod = df["time"].dt.time
    sess = df[(tod >= dtime(9, 30)) & (tod <= dtime(16, 0))].copy()
    sess["date"] = sess["time"].dt.date
    return sess.reset_index(drop=True)


def run(csv_path, cfg=None, slippage=1.0):
    cfg = cfg or Config()
    df = load_session(csv_path)
    trades, wins, pnl_total = 0, 0, 0.0
    for _date, day in df.groupby("date", sort=True):
        day = day.reset_index(drop=True)
        n = len(day)
        i = max(cfg.ema_trend + 2, cfg.orb_minutes + 2)
        last_exit_t = None
        # Same risk guard as live trading: halt the day at +$500 / -$1,000.
        guard = DailyGuard(cfg.daily_profit_target, cfg.max_daily_loss)
        while i < n:
            t = day["time"].iloc[i]
            if (last_exit_t is not None
                    and t - last_exit_t < timedelta(minutes=cfg.cooldown_minutes)):
                i += 1
                continue
            if guard.halted():
                break  # daily stop/target hit: done for the day, like live
            window = day.iloc[: i + 1]  # today's bars only -> daily VWAP/ORB
            sigs = check_signals(window, cfg)
            if not sigs:
                i += 1
                continue
            side, _reason = sigs[0]
            entry_px = day["close"].iloc[i]
            a = atr(window, cfg.atr_period).iloc[-1]
            qty = contracts_for_risk(a, cfg.risk_per_trade)
            d = 1 if side == "CALL" else -1
            exit_px, j = None, i + 1
            while j < n:
                move = (day["close"].iloc[j] - entry_px) * d
                if move >= cfg.atr_target_mult * a:
                    exit_px = day["close"].iloc[j]
                    break
                if move <= -cfg.atr_stop_mult * a:
                    exit_px = day["close"].iloc[j]
                    break
                j += 1
            if exit_px is None:
                exit_px, j = day["close"].iloc[n - 1], n
            move = (exit_px - entry_px) * d
            pnl = move * 0.5 * 100 * qty - 2 * slippage * qty
            trades += 1
            wins += pnl > 0
            pnl_total += pnl
            guard.pnl += pnl
            last_exit_t = day["time"].iloc[min(j, n - 1)]
            i = j + 1 if j < n else n
    wr = wins / trades if trades else 0
    print(f"trades={trades} win_rate={wr:.1%} pnl=${pnl_total:,.2f}")
    return {"trades": trades, "win_rate": wr, "pnl": pnl_total}


if __name__ == "__main__":
    run(sys.argv[1])
