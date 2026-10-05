"""Risk: sizing, ATR exits, daily guardrails, cooldowns."""
from datetime import timedelta


class DailyGuard:
    """Halts trading for the day at the profit target or max loss."""

    def __init__(self, target, max_loss):
        self.target, self.max_loss = target, max_loss
        self.day, self.pnl = None, 0.0

    def reset_if_new_day(self, now):
        if self.day != now.date():
            self.day, self.pnl = now.date(), 0.0

    def halted(self):
        return self.pnl >= self.target or self.pnl <= self.max_loss


def contracts_for_risk(atr_value, risk_dollars, delta=0.5):
    """ATM option moves ~delta x underlying. Size so a 1x ATR adverse
    move loses roughly risk_dollars."""
    per_contract = atr_value * delta * 100
    if per_contract <= 0:
        return 1
    return max(1, int(risk_dollars // per_contract))


class Cooldowns:
    """One cooldown timer per worker key."""

    def __init__(self, minutes):
        self.minutes, self.last = minutes, {}

    def ready(self, key, now):
        t = self.last.get(key)
        return t is None or now - t >= timedelta(minutes=self.minutes)

    def mark(self, key, now):
        self.last[key] = now


class Position:
    def __init__(self, occ, side, qty, entry_premium, underlying_entry, atr):
        self.occ = occ
        self.side = side  # CALL or PUT
        self.qty = qty
        self.entry_premium = entry_premium
        self.underlying_entry = underlying_entry
        self.atr = atr

    def exit_reason(self, underlying_now, cfg):
        d = 1 if self.side == "CALL" else -1
        move = (underlying_now - self.underlying_entry) * d
        if move >= cfg.atr_target_mult * self.atr:
            return "atr-target"
        if move <= -cfg.atr_stop_mult * self.atr:
            return "atr-stop"
        return None

    def est_pnl(self, underlying_now, delta=0.5):
        """Estimated P&L. Live mode should replace with actual fill prices."""
        d = 1 if self.side == "CALL" else -1
        move = (underlying_now - self.underlying_entry) * d
        return move * delta * 100 * self.qty
