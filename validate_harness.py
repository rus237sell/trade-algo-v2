"""Harness validation: KNOWN-EDGE signals through the real backtest engine.

validate_framework.py checks arithmetic (buy-hold, random R:R math) but never
touches backtest_v2.py or the strategy code. These tests feed a signal with a
KNOWN outcome through the engine's actual fill/exit path. If the harness can't
reproduce a known edge, the harness -- not the strategy -- is broken.

Tests:
0. Mirror check: this script's runner vs backtest_v2.run with an always-long
   signal -> trade counts and P&L must match EXACTLY. (Proves the runner below
   faithfully mirrors the engine's fill/exit conventions.)
1. Perfect 5-bar foresight, fixed-horizon exit -> win rate MUST be 100.0%.
   (Anything less = fill/exit misalignment, off-by-one, or lookahead leak.)
2. Anti-foresight (always the wrong side), fixed-horizon -> MUST be 0.0%.
3. Perfect foresight through the REAL ATR target/stop exits -> win rate far
   above the 40% random baseline and P&L positive. (Exercises the true exit
   path: ATR target, ATR stop, and the EOD flatten.)

Conventions mirrored from backtest_v2.py: fill at signal-bar close, ATR exits
scanned on subsequent closes (target checked before stop), EOD flatten at the
last session bar, one position at a time, $1/contract/side slippage, option
moves ~0.5x the underlying move. Test 0 mirrors the engine exactly (start bar
52, 5-min cooldown); tests 1-3 use every-bar entries with no cooldown.

Usage: python validate_harness.py data/SPY_1min.csv
"""
import sys
from datetime import timedelta

import backtest_v2
from backtest_v2 import load_session
from config import Config
from signals import atr
from risk import contracts_for_risk

HORIZON = 5  # foresight horizon in bars


def run_mirror(csv_path, signal_fn, exit_mode="atr", cfg=None, slippage=1.0,
               start_i=20, use_cooldown=False):
    """Minimal runner mirroring backtest_v2.run's fill/exit conventions."""
    cfg = cfg or Config()
    df = load_session(csv_path)
    trades, wins, losses, pnl_total = 0, 0, 0, 0.0
    for _date, day in df.groupby("date", sort=True):
        day = day.reset_index(drop=True)
        n = len(day)
        i = start_i
        last_exit_t = None
        day_pnl = 0.0
        while i < n - HORIZON:
            if day_pnl >= cfg.daily_profit_target or day_pnl <= cfg.max_daily_loss:
                break
            t = day["time"].iloc[i]
            if (use_cooldown and last_exit_t is not None
                    and t - last_exit_t < timedelta(minutes=cfg.cooldown_minutes)):
                i += 1
                continue
            side = signal_fn(day, i)
            if side is None:
                i += 1
                continue
            d = 1 if side == "CALL" else -1
            entry_px = day["close"].iloc[i]
            if exit_mode == "fixed":
                exit_px = day["close"].iloc[i + HORIZON]
                j = i + HORIZON
                pnl = (exit_px - entry_px) * d  # qty=1, no slippage: pure direction check
            else:  # "atr": same exit logic as backtest_v2.run
                window = day.iloc[: i + 1]
                a = atr(window, cfg.atr_period).iloc[-1]
                qty = contracts_for_risk(a, cfg.risk_per_trade)
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
            losses += pnl < 0
            pnl_total += pnl
            day_pnl += pnl
            last_exit_t = day["time"].iloc[min(j, n - 1)]
            i = j + 1 if j < n else n
    wr = wins / trades if trades else 0
    return {"trades": trades, "win_rate": wr, "losses": losses, "pnl": pnl_total}


def perfect_foresight(day, i):
    return "CALL" if day["close"].iloc[i + HORIZON] > day["close"].iloc[i] else "PUT"


def anti_foresight(day, i):
    return "PUT" if day["close"].iloc[i + HORIZON] > day["close"].iloc[i] else "CALL"


def main(csv_path):
    print(f"Validating harness on {csv_path}")
    print("=" * 60)

    # Test 0: mirror check vs the real engine (always-long signal).
    # backtest_v2 starts at max(ema_trend+2, orb_minutes+2)=52 with a 5-min cooldown.
    backtest_v2.check_signals = lambda window, cfg: [("CALL", "always-long")]
    r = backtest_v2.run(csv_path)
    real = (r["trades"], round(r["pnl"], 2))
    m = run_mirror(csv_path, lambda day, i: "CALL", exit_mode="atr",
                   start_i=52, use_cooldown=True)
    mine = (m["trades"], round(m["pnl"], 2))
    ok0 = real == mine
    print(f"0. Mirror check: engine={real} runner={mine}")
    print(f"   {'PASS' if ok0 else 'FAIL - runner does not mirror backtest_v2'}")
    print()
    if not ok0:
        print("Runner diverged from the engine; aborting further tests.")
        return

    # Test 1: perfect foresight, fixed horizon -> zero losses.
    # (Flat 5-bar moves tie at pnl=0 and don't count as wins; only losses fail.)
    m = run_mirror(csv_path, perfect_foresight, exit_mode="fixed")
    ok1 = m["losses"] == 0
    print(f"1. Perfect {HORIZON}-bar foresight (fixed exit): {m['trades']} trades, "
          f"{m['win_rate']:.1%} WR, {m['losses']} losses")
    print(f"   {'PASS' if ok1 else 'FAIL - fill/exit misalignment or lookahead leak'}")
    print()

    # Test 2: anti-foresight -> zero wins.
    m = run_mirror(csv_path, anti_foresight, exit_mode="fixed")
    ok2 = m["win_rate"] == 0.0
    print(f"2. Anti-foresight (fixed exit): {m['trades']} trades, {m['win_rate']:.1%} WR")
    print(f"   {'PASS' if ok2 else 'FAIL - exits do not track entries'}")
    print()

    # Test 3: perfect foresight through the real ATR exits
    m = run_mirror(csv_path, perfect_foresight, exit_mode="atr")
    ok3 = m["win_rate"] > 0.55 and m["pnl"] > 0
    print(f"3. Perfect foresight (ATR exits): {m['trades']} trades, {m['win_rate']:.1%} WR, "
          f"pnl=${m['pnl']:,.2f}")
    print(f"   {'PASS' if ok3 else 'FAIL - exit path broken'} (expect WR >> 40% random baseline)")
    print("=" * 60)


if __name__ == "__main__":
    main(sys.argv[1])
