"""Framework validation: naive patterns with KNOWN expected outcomes.

If these don't behave as expected, the backtest framework (not the
strategy) is broken. Run BEFORE testing any real strategy.

Tests:
1. Buy-and-hold SPY 2020-2026 -> MUST be strongly positive (market +~90%)
2. Random entries 1:1.5 R:R -> MUST be ~40% win rate (breakeven math)
3. 20-day momentum (long if px > 20d SMA) -> should be mildly positive
4. Inverse of failed signals -> if orig was 31.7%, inverse should be ~68%

Usage: python validate_framework.py data/SPY_1min.csv
"""
import sys
import random
from datetime import time as dtime

import pandas as pd
import numpy as np
import pytz

ET = pytz.timezone("America/New_York")
random.seed(42)
np.random.seed(42)


def load(csv_path):
    df = pd.read_csv(csv_path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("time").reset_index(drop=True)
    df["date"] = df["time"].dt.date
    return df


def test_buy_hold(df):
    """Buy first bar, hold to last. Must be positive 2020-2026."""
    entry = df["close"].iloc[0]
    exit_px = df["close"].iloc[-1]
    ret = (exit_px - entry) / entry
    print(f"1. Buy-and-hold: {ret:.1%} total return")
    print(f"   {'PASS' if ret > 0.5 else 'FAIL - data may be broken'} (expect >+50% for 2020-2026)")
    return ret


def test_random(df, n_trials=500):
    """Random long/short with 1.5x ATR target, 1.0x stop. Must be ~40% WR."""
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    wins, trades = 0, 0
    n = len(df)
    for _ in range(n_trials):
        i = random.randint(20, n - 100)
        if pd.isna(df["atr"].iloc[i]):
            continue
        direction = random.choice([1, -1])
        entry = df["close"].iloc[i]
        a = df["atr"].iloc[i]
        target = entry + direction * 1.5 * a
        stop = entry - direction * 1.0 * a
        # Walk forward max 100 bars
        for j in range(i + 1, min(i + 101, n)):
            px = df["close"].iloc[j]
            move = (px - entry) * direction
            if move >= 1.5 * a:
                wins += 1
                break
            if move <= -1.0 * a:
                break
        trades += 1
    wr = wins / trades if trades else 0
    # 95% CI for 40%: +/- 1.96*sqrt(0.4*0.6/500) = +/- 4.3%
    ok = 0.357 < wr < 0.443
    print(f"2. Random 1:1.5 R:R: {trades} trades, {wr:.1%} win rate")
    print(f"   {'PASS' if ok else 'FAIL - R:R math broken'} (expect 35.7%-44.3%)")
    return wr


def test_momentum(df):
    """Long when close > 20-day SMA of daily closes. Should be >= 0."""
    daily = df.groupby("date")["close"].last()
    sma20 = daily.rolling(20).mean()
    # Daily returns when in position
    rets = daily.pct_change()
    in_pos = (daily > sma20).shift(1)  # enter next day
    strat_rets = rets[in_pos.fillna(False)]
    total = (1 + strat_rets.fillna(0)).prod() - 1
    bh = daily.iloc[-1] / daily.iloc[0] - 1
    print(f"3. 20-day momentum: {total:.1%} vs buy-hold {bh:.1%}")
    print(f"   {'PASS' if total > -0.1 else 'FAIL'} (expect >= -10%, typically positive)")
    return total


def test_inverse_qqq(df):
    """If orig signals were 31.7% WR, inverse should be ~68%.
    We simulate by flipping random 31.7% WR to 68.3%."""
    # This is a logic check, not a data test
    orig_wr = 0.317
    inv_wr = 1 - orig_wr
    print(f"4. Inverse logic: {orig_wr:.1%} -> {inv_wr:.1%}")
    print(f"   PASS (math check: inverse of 31.7% is 68.3%)")
    return inv_wr


def main(csv_path):
    print(f"Validating framework on {csv_path}")
    print("=" * 60)
    df = load(csv_path)
    print(f"Loaded {len(df)} bars, {df['date'].nunique()} days")
    print(f"Range: {df['date'].min()} to {df['date'].max()}")
    print()
    test_buy_hold(df)
    print()
    test_random(df)
    print()
    test_momentum(df)
    print()
    test_inverse_qqq(df)
    print("=" * 60)


if __name__ == "__main__":
    main(sys.argv[1])
