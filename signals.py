"""Signal logic: pure functions over 1-min bars.

df columns: time (datetime), open, high, low, close, volume.
"""
import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n).mean()


def _crossed_up(a, b):
    return a.iloc[-1] > b.iloc[-1] and a.iloc[-2] <= b.iloc[-2]


def _crossed_dn(a, b):
    return a.iloc[-1] < b.iloc[-1] and a.iloc[-2] >= b.iloc[-2]


def check_signals(df, cfg):
    """Signals for the latest closed bar. Returns list of (side, reason)."""
    need = max(cfg.ema_trend + 2, cfg.orb_minutes + 2)
    if len(df) < need:
        return []
    close = df["close"]
    e9 = ema(close, cfg.ema_fast)
    e21 = ema(close, cfg.ema_slow)
    e50 = ema(close, cfg.ema_trend)
    v = vwap(df)

    today = df["time"].dt.date == df["time"].iloc[-1].date()
    orb = df[today].iloc[:cfg.orb_minutes]
    orb_h, orb_l = orb["high"].max(), orb["low"].min()
    squeezed = abs(e9.iloc[-1] - e21.iloc[-1]) / close.iloc[-1] < cfg.squeeze_pct

    out = []
    # 1. VWAP cross (skipped when 9/21 EMAs are squeezed = chop)
    if not squeezed:
        if _crossed_up(close, v):
            out.append(("CALL", "vwap-cross-up"))
        elif _crossed_dn(close, v):
            out.append(("PUT", "vwap-cross-dn"))
    # 2. 50 EMA cross
    if _crossed_up(close, e50):
        out.append(("CALL", "ema50-cross-up"))
    elif _crossed_dn(close, e50):
        out.append(("PUT", "ema50-cross-dn"))
    # 3. Opening-range breakout (first 15 min high/low)
    if close.iloc[-1] > orb_h and close.iloc[-2] <= orb_h:
        out.append(("CALL", "orb-breakout-up"))
    elif close.iloc[-1] < orb_l and close.iloc[-2] >= orb_l:
        out.append(("PUT", "orb-breakdown-dn"))
    return out
