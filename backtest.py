"""Backtest the v2 signals on a CSV of 1-min bars.

CSV columns: time,open,high,low,close,volume
Fills simulated at the signal bar close; exits on ATR target/stop or EOD.
Slippage: flat $1 per contract per side.

Usage: python backtest.py bars.csv
"""
import sys
import pandas as pd

from config import Config
from signals import check_signals, atr
from risk import contracts_for_risk


def run(csv_path, cfg=None, slippage=1.0):
    cfg = cfg or Config()
    df = pd.read_csv(csv_path)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    trades, wins, pnl_total = 0, 0, 0.0
    i = max(cfg.ema_trend + 2, cfg.orb_minutes + 2)
    while i < len(df):
        window = df.iloc[: i + 1]
        sigs = check_signals(window, cfg)
        if not sigs:
            i += 1
            continue
        side, reason = sigs[0]
        entry_px = df["close"].iloc[i]
        a = atr(window, cfg.atr_period).iloc[-1]
        qty = contracts_for_risk(a, cfg.risk_per_trade)
        d = 1 if side == "CALL" else -1
        exit_px, exit_reason = None, "eod"
        for j in range(i + 1, len(df)):
            if df["time"].iloc[j].date() != df["time"].iloc[i].date():
                break
            move = (df["close"].iloc[j] - entry_px) * d
            if move >= cfg.atr_target_mult * a:
                exit_px, exit_reason = df["close"].iloc[j], "target"
                break
            if move <= -cfg.atr_stop_mult * a:
                exit_px, exit_reason = df["close"].iloc[j], "stop"
                break
        if exit_px is None:
            exit_px = df["close"].iloc[j - 1]
        move = (exit_px - entry_px) * d
        pnl = move * 0.5 * 100 * qty - 2 * slippage * qty
        trades += 1
        wins += pnl > 0
        pnl_total += pnl
        i = j + 1

    print(f"trades={trades} win_rate={wins / trades if trades else 0:.1%} "
          f"pnl=${pnl_total:,.2f}")
    return {"trades": trades, "win_rate": wins / trades if trades else 0,
            "pnl": pnl_total}


if __name__ == "__main__":
    run(sys.argv[1])
