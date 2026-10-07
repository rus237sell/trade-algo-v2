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
from broker import DryRunBroker, TradierBroker, occ_symbol

ET = pytz.timezone("America/New_York")


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
        # Estimate premium for sizing log (live: use quote)
        premium = max(0.5, 0.5 * a)
        self.broker.place(occ, side, qty, tag=f"v2:{reason}")
        self.positions[symbol] = Position(occ, side, qty, premium, px, a)
        self.cooldowns.mark(symbol, now)
        print(f"ENTER {symbol} {side} {qty}x {occ} strike={strike} ({reason})")

    def _exit(self, symbol, pos, reason, px):
        pnl = pos.est_pnl(px)
        self.broker.close(pos.occ, pos.qty, tag=f"v2:{reason}")
        self.guard.pnl += pnl
        del self.positions[symbol]
        print(f"EXIT {symbol} {pos.side} ({reason}) est_pnl={pnl:+.2f} day={self.guard.pnl:+.2f}")

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
                {"symbol": s, "side": p.side, "qty": p.qty, "occ": p.occ}
                for s, p in self.positions.items()
            ],
            "data_lag_min": round(max_lag_min, 1) if max_lag_min is not None else None,
            "broker": os.getenv("BROKER", self.cfg.broker),
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
        state = self._session_state(now)

        if state == "flatten":
            for sym, pos in list(self.positions.items()):
                df = self._day_bars(sym, now)
                px = df["close"].iloc[-1] if df is not None else pos.underlying_entry
                self._exit(sym, pos, "flatten-eod", px)
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
            df = self._day_bars(symbol, now)
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
                    self._exit(symbol, pos, reason, px)
                continue

            # Entries
            if state != "open":
                continue
            if stale:
                continue  # fail-safe: manage exits only until data recovers
            if self.guard.halted():
                print(f"HALTED for day (pnl={self.guard.pnl:+.2f})")
                return
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
