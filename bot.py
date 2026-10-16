"""trade-algo-v2 runner: N workers, 1-min signals, 0DTE ATM options, paper.

Usage:
    python bot.py            # dry-run (logs orders, needs no keys)
    BROKER=tradier python bot.py   # Tradier sandbox paper trading
"""
import os
import time
import json
from datetime import datetime, time as dtime

import pandas as pd
import pytz

from config import Config
from data import TradierData
from alpaca import AlpacaData, AlpacaBroker
from signals import check_signals, atr
from risk import DailyGuard, Cooldowns, Position, contracts_for_risk
from broker import DryRunBroker, TradierBroker, occ_symbol, parse_occ

ET = pytz.timezone("America/New_York")


def _git_sha():
    """Short SHA of the checked-out code, stamped into state.json so
    monitoring can always see which code is actually running (whole-
    file overwrites have silently reverted fixes here before)."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


GIT_SHA = _git_sha()


def parse_t(s):
    h, m = map(int, s.split(":"))
    return dtime(h, m)


class Bot:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        broker_name = os.getenv("BROKER", self.cfg.broker)
        if broker_name == "alpaca":
            try:
                self.broker = AlpacaBroker()
                print("Broker: Alpaca paper (free)")
            except RuntimeError as e:
                print(f"Alpaca unavailable ({e}); falling back to dry-run")
                self.broker = DryRunBroker()
        elif broker_name == "tradier":
            try:
                self.broker = TradierBroker(env=self.cfg.tradier_env)
                print("Broker: Tradier (paid)")
            except RuntimeError as e:
                print(f"Tradier unavailable ({e}); falling back to dry-run")
                self.broker = DryRunBroker()
        else:
            self.broker = DryRunBroker()
            print("Broker: dry-run (no real orders)")
        source = os.getenv("DATA_SOURCE", self.cfg.data_source)
        if source == "alpaca":
            self.data = AlpacaData()
            print("Data: Alpaca (free, 15-min delayed)")
        else:
            self.data = TradierData(env=self.cfg.tradier_env)
        self.guard = DailyGuard(self.cfg.daily_profit_target, self.cfg.max_daily_loss)
        self.cooldowns = Cooldowns(self.cfg.cooldown_minutes)
        self.positions = {}  # symbol -> Position
        self._last_lag_warn = None
        self._last_stale_warn = None
        self._last_halt_warn = None
        self._recover_positions()

    def _ledger_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "trades.jsonl")

    def _log_trade(self, record):
        """Append one JSON line to trades.jsonl — an append-only ledger of
        every entry and exit (estimates, not broker fills), so a day's
        trading can be reviewed or reconciled against the broker after the
        fact. Ledger is monitoring-only: failures here never stop the bot."""
        record = dict(record)
        record.setdefault("time", datetime.now(ET).isoformat())
        try:
            with open(self._ledger_path(), "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass

    def _recover_positions(self):
        """Re-adopt open 0DTE positions after a restart so ATR exits and the
        EOD flatten still cover them. Only today's-expiry long option legs
        on strategy symbols are recovered; expired leftovers are ignored.
        A broker outage here must never stop the bot, so errors are logged
        and swallowed."""
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return
        today = now.strftime("%y%m%d")
        try:
            open_pos = self.broker.positions()
        except Exception as e:
            print(f"position recovery skipped (broker error): {e}")
            return
        for p in open_pos:
            occ = p.get("occ") or p.get("symbol", "")
            try:
                root, yymmdd, cp, _strike = parse_occ(occ)
            except ValueError:
                continue  # stock positions / non-OCC legs: not ours
            qty = int(float(p.get("qty", 0)))
            if (root not in self.cfg.symbols or yymmdd != today
                    or qty <= 0 or p.get("side", "long") != "long"):
                continue  # expired contracts, shorts, or other books
            df = self._day_bars(root, now)
            if df is None or len(df) < self.cfg.atr_period:
                continue
            px = float(df["close"].iloc[-1])
            a = float(atr(df, self.cfg.atr_period).iloc[-1])
            pos = Position(occ, "CALL" if cp == "C" else "PUT",
                           qty, None, px, a, recovered=True)
            self.positions[root] = pos
            self.cooldowns.mark(root, now)  # settle before re-entering
            print(f"RECOVERED {root} {pos.side} {qty}x {occ} "
                  f"(entry unknown after restart; exits measured from {px:.2f})")

    def _session_state(self, now):
        if now.weekday() >= 5:
            return "closed"
        t = now.time()
        if t < parse_t(self.cfg.trade_start):
            return "pre"
        if t >= parse_t(self.cfg.flatten_time):
            return "flatten"
        if t >= parse_t(self.cfg.trade_end):
            return "no-entries"
        return "open"

    def _day_bars(self, symbol, now):
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        raw = self.data.bars_1min(symbol, start, now)
        if not raw:
            return None
        df = pd.DataFrame(raw)
        df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                                "close": "close", "volume": "volume", "time": "time"})
        df["time"] = pd.to_datetime(df["time"])
        return df.sort_values("time").reset_index(drop=True)

    def _pick_occ(self, symbol, side, underlying_px, today):
        exp = today.strftime("%Y%m%d")
        cp = "C" if side == "CALL" else "P"
        if isinstance(self.data, AlpacaData):
            contracts = self.data.option_contracts(
                symbol, today.strftime("%Y-%m-%d"), "call" if cp == "C" else "put")
            if contracts:
                c = min(contracts,
                        key=lambda o: abs(float(o["strike_price"]) - underlying_px))
                return c["symbol"], float(c["strike_price"])
        else:
            chain = self.data.chain(symbol, today.strftime("%Y-%m-%d"))
            if chain:
                strikes = sorted({float(o["strike"]) for o in chain})
                strike = min(strikes, key=lambda s: abs(s - underlying_px))
                return occ_symbol(symbol, exp, cp, strike), strike
        strike = round(underlying_px)  # dry-run fallback
        return occ_symbol(symbol, exp, cp, strike), strike

    def _enter(self, symbol, side, reason, df, now):
        px = df["close"].iloc[-1]
        a = atr(df, self.cfg.atr_period).iloc[-1]
        qty = contracts_for_risk(a, self.cfg.risk_per_trade)
        occ, strike = self._pick_occ(symbol, side, px, now.date())
        # Never buy a contract with no live quote: a quoteless far-OTM
        # contract can be bought for $0.01 but then cannot be sold at
        # all (Alpaca rejects market sells when no quote is available).
        if isinstance(self.data, AlpacaData):
            bid, ask = self.data.option_quote(occ)
            if bid is None or ask is None:
                print(f"ENTER SKIPPED {symbol} {side} ({reason}): "
                      f"no two-sided quote for {occ} (bid={bid} ask={ask})")
                return
        # Estimate premium for sizing log (live: use quote)
        premium = max(0.5, 0.5 * a)
        # client_order_id must be unique per order intent: a reason-only
        # tag collides with any earlier order that used it (e.g. an
        # "atr-stop" exit from a previous day) and Alpaca rejects the
        # order with 422. Symbol+reason+minute keeps a retry inside the
        # same minute idempotent without burning the tag forever, and a
        # rejected order must never abort the cycle, so log and skip.
        try:
            order = self.broker.place(
                occ, side, qty,
                tag=f"v2:{symbol}:{reason}:{now:%Y%m%d%H%M}")
        except Exception as e:
            print(f"ENTER FAILED {symbol} ({reason}): {e}")
            return
        # Accepted is not filled: a terminal-unfilled entry order must
        # not create a phantom position the broker does not hold.
        if isinstance(self.broker, AlpacaBroker) and isinstance(order, dict) \
                and order.get("id"):
            filled = self.broker.wait_fill(order["id"], timeout=15)
            if filled is not None and filled.get("status") in (
                    "canceled", "expired", "rejected", "suspended") \
                    and float(filled.get("filled_qty") or 0) == 0:
                print(f"ENTER NOT FILLED {symbol} ({reason}): order "
                      f"{order['id']} ended {filled.get('status')} unfilled")
                return
        self.positions[symbol] = Position(occ, side, qty, premium, px, a)
        self.cooldowns.mark(symbol, now)
        self._log_trade({"event": "entry", "symbol": symbol, "side": side,
                         "qty": qty, "occ": occ, "strike": strike,
                         "reason": reason, "underlying_px": float(px),
                         "atr": float(a), "est_premium": float(premium)})
        print(f"ENTER {symbol} {side} {qty}x {occ} strike={strike} ({reason})")

    def _exit(self, symbol, pos, reason, px, now):
        # An exit order already working (e.g. the $0.01 limit placed
        # when the contract had no quote) owns this position: poll it
        # instead of stacking another order on top.
        if getattr(pos, "exit_order_id", None):
            self._poll_exit(symbol, pos, px)
            return
        # Unique-per-intent tag (see _enter): contract+reason+minute.
        # A rejected close logs and leaves the position in place for a
        # retry instead of aborting the cycle and freezing the state.
        try:
            order = self.broker.close(
                pos.occ, pos.qty,
                tag=f"v2:exit:{pos.occ}:{reason}:{now:%H%M}")
        except Exception as e:
            print(f"EXIT FAILED {symbol} ({reason}): {e}")
            return
        # Accepted is not filled: an exit only counts once the broker
        # reports the fill. A market order on a quoted contract fills
        # in seconds; a quoteless-contract limit order may rest, so
        # park it on the position and let later cycles poll it rather
        # than dropping the position while the broker still holds it
        # (an orphaned long 0DTE can even auto-exercise into stock).
        if isinstance(self.broker, AlpacaBroker) and isinstance(order, dict) \
                and order.get("id"):
            filled = self.broker.wait_fill(order["id"], timeout=15)
            if filled is not None and filled.get("status") == "filled":
                self._finalize_exit(symbol, pos, reason, px, filled)
                return
            if filled is not None and filled.get("status") in (
                    "canceled", "expired", "rejected", "suspended") \
                    and float(filled.get("filled_qty") or 0) == 0:
                print(f"EXIT NOT FILLED {symbol} ({reason}): order "
                      f"{order['id']} ended {filled.get('status')} "
                      f"unfilled; position held for retry")
                return
            pos.exit_order_id = order["id"]
            pos.exit_order_ts = time.time()
            pos.exit_reason = reason
            print(f"EXIT PENDING {symbol} ({reason}): order {order['id']} "
                  f"accepted, not filled yet; position held")
            return
        self._finalize_exit(symbol, pos, reason, px, None)

    def _finalize_exit(self, symbol, pos, reason, px, order):
        """Book an exit the broker has actually filled (or, for non-
        Alpaca brokers, an accepted close as before). Prefers the real
        fill price for P&L when the broker reports one."""
        fill_px = None
        if order is not None and order.get("filled_avg_price"):
            try:
                fill_px = float(order["filled_avg_price"])
            except (TypeError, ValueError):
                fill_px = None
        if fill_px is not None and pos.entry_premium:
            pnl = (fill_px - pos.entry_premium) * pos.qty * 100
        else:
            pnl = pos.est_pnl(px)
        self.guard.pnl += pnl
        self._log_trade({"event": "exit", "symbol": symbol, "side": pos.side,
                         "qty": pos.qty, "occ": pos.occ, "reason": reason,
                         "underlying_px": float(px),
                         "est_pnl": float(round(pnl, 2)),
                         "fill_price": fill_px,
                         "day_pnl": float(round(self.guard.pnl, 2)),
                         "recovered": bool(getattr(pos, "recovered", False))})
        if self.positions.get(symbol) is pos:
            del self.positions[symbol]
        print(f"EXIT {symbol} {pos.side} ({reason}) pnl={pnl:+.2f} "
              f"day={self.guard.pnl:+.2f}"
              + (f" fill={fill_px:.2f}" if fill_px is not None else " (est)"))

    def _poll_exit(self, symbol, pos, px):
        oid = pos.exit_order_id
        order = self.broker.get_order(oid)
        if order is None:
            return  # transient read failure; try again next cycle
        status = order.get("status")
        if status == "filled" or (
                status in ("canceled", "expired", "rejected", "suspended")
                and float(order.get("filled_qty") or 0) > 0):
            self._finalize_exit(symbol, pos,
                                getattr(pos, "exit_reason", "exit"),
                                px, order)
            return
        if status in ("canceled", "expired", "rejected", "suspended"):
            print(f"EXIT ORDER ENDED {symbol}: {oid} {status} unfilled; "
                  f"position held, exit re-evaluated next cycle")
            pos.exit_order_id = None
            return
        # Still working: cancel/replace if it has rested too long so a
        # stale limit cannot pin the position while the market moves.
        if time.time() - getattr(pos, "exit_order_ts", 0) > 120:
            self.broker.cancel_order(oid)
            pos.exit_order_id = None
            print(f"EXIT ORDER STALE {symbol}: cancelled {oid}; "
                  f"a fresh exit will be placed when the exit rule fires")

    def _save_state(self, now, session_state, max_lag_min, stale_data=False):
        """Write a small status file every cycle for status.py / monitoring."""
        state = {
            "time": now.isoformat(),
            "session": session_state,
            # float()/bool(): guard math can hold numpy scalars (est_pnl runs on
            # pandas floats), which json can't serialize — coerce here so the
            # state file never gets truncated mid-write.
            "day_pnl": float(round(self.guard.pnl, 2)),
            "halted": bool(self.guard.halted()),
            "stale_data": stale_data,  # fail-safe: entries blocked on dead feed
            "positions": [
                {"symbol": s, "side": p.side, "qty": p.qty, "occ": p.occ,
                 "recovered": bool(getattr(p, "recovered", False))}
                for s, p in self.positions.items()
            ],
            "data_lag_min": round(max_lag_min, 1) if max_lag_min is not None else None,
            "broker": os.getenv("BROKER", self.cfg.broker),
            "git_sha": GIT_SHA,
        }
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "state.json"), "w") as f:
                json.dump(state, f)
        except Exception:
            pass
        if max_lag_min is not None and max_lag_min > 5 and (
                self._last_lag_warn is None
                or (now - self._last_lag_warn).total_seconds() > 900):
            print(f"WARNING: market data is {max_lag_min:.0f} min stale — "
                  f"signals are delayed (free tier).")
            self._last_lag_warn = now

    def _stale_check(self, now, state, max_lag):
        """Fail-safe: block new entries when the data feed is dead.

        Returns True when entries should be blocked. Exits and the EOD
        flatten keep running so open positions are never stranded."""
        stale = (state == "open" and max_lag is not None
                 and max_lag > self.cfg.max_data_lag_min)
        if stale and (self._last_stale_warn is None
                      or (now - self._last_stale_warn).total_seconds() > 300):
            print(f"STALE DATA: freshest bar {max_lag:.0f} min old "
                  f"(limit {self.cfg.max_data_lag_min:g} min) — "
                  f"new entries blocked; exits/flatten still run.")
            self._last_stale_warn = now
        return stale

    def cycle(self):
        now = datetime.now(ET)
        self.guard.reset_if_new_day(now)
        # Drop positions whose contract expired on an earlier day (a
        # flatten order that never filled): they can never exit, and
        # exit retries against a dead contract would wedge forever.
        today_yymmdd = now.strftime("%y%m%d")
        for sym, pos in list(self.positions.items()):
            try:
                _root, pos_yymmdd, _cp, _strike = parse_occ(pos.occ)
            except ValueError:
                continue
            if pos_yymmdd != today_yymmdd:
                print(f"PRUNE {sym}: dropping expired position {pos.occ}")
                del self.positions[sym]
        state = self._session_state(now)

        if state == "flatten":
            for sym, pos in list(self.positions.items()):
                df = self._day_bars(sym, now)
                px = df["close"].iloc[-1] if df is not None else pos.underlying_entry
                self._exit(sym, pos, "flatten-eod", px, now)
            self._save_state(now, state, None)
            return

        if state in ("closed", "pre"):
            self._save_state(now, state, None)
            return

        # Fetch today's bars once per symbol, measure data lag first so a
        # dead feed blocks new entries before any signal is acted on.
        bars = {}
        lags = []
        for symbol in self.cfg.symbols:
            try:
                df = self._day_bars(symbol, now)
            except Exception as e:
                print(f"bars error ({symbol}): {e}")
                continue
            if df is None or len(df) < 3:
                continue
            bars[symbol] = df
            lag = (now - df["time"].iloc[-1].to_pydatetime()).total_seconds() / 60
            lags.append(lag)

        max_lag = max(lags) if lags else None
        stale = self._stale_check(now, state, max_lag)

        for symbol, df in bars.items():
            px = df["close"].iloc[-1]

            # Manage open position
            if symbol in self.positions:
                pos = self.positions[symbol]
                reason = pos.exit_reason(px, self.cfg)
                if reason:
                    self._exit(symbol, pos, reason, px, now)
                continue

            # Entries
            if state != "open":
                continue
            if stale:
                continue  # fail-safe: manage exits only until data recovers
            if self.guard.halted():
                # Skip entries only: returning here would also skip
                # exit management for the remaining symbols and the
                # end-of-cycle state save, freezing the dashboard.
                if self._last_halt_warn is None or (
                        now - self._last_halt_warn).total_seconds() > 300:
                    print(f"HALTED for day (pnl={self.guard.pnl:+.2f})")
                    self._last_halt_warn = now
                continue
            if len(self.positions) >= self.cfg.max_workers:
                continue
            if not self.cooldowns.ready(symbol, now):
                continue
            for side, reason in check_signals(df, self.cfg):
                self._enter(symbol, side, reason, df, now)
                break  # one entry per symbol per cycle

        self._save_state(now, state, max_lag, stale_data=stale)

    def run(self):
        print("trade-algo-v2 running. Ctrl+C to stop.")
        while True:
            try:
                self.cycle()
            except Exception as e:
                print(f"cycle error: {e}")
            time.sleep(self.cfg.poll_seconds)


if __name__ == "__main__":
    Bot().run()
