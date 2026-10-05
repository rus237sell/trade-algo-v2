"""trade-algo-v2 configuration. All tunables live here."""
from dataclasses import dataclass, field


@dataclass
class Config:
    # Universe: 0DTE scalps on index ETFs
    symbols: list = field(default_factory=lambda: ["QQQ", "SPY", "IWM"])
    # Workers: max concurrent positions (the "towers" in Scalp City)
    max_workers: int = 5

    # Risk
    risk_per_trade: float = 313.0      # $ risked per trade (quarter size)
    daily_profit_target: float = 500.0  # halt all trading for the day here
    max_daily_loss: float = -1000.0      # hard stop for the day

    # Strategy (1-minute bars)
    orb_minutes: int = 15               # opening-range window
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 50
    atr_period: int = 14
    atr_stop_mult: float = 1.0           # exit when underlying moves 1.0x ATR against
    atr_target_mult: float = 1.5         # exit when underlying moves 1.5x ATR in favor
    squeeze_pct: float = 0.0005          # skip VWAP cross if |EMA9-EMA21|/price < this
    cooldown_minutes: int = 5           # wait after each trade before next signal

    # Session (US Eastern)
    trade_start: str = "09:45"          # entries begin (ORB formed)
    trade_end: str = "15:30"            # no new entries after
    flatten_time: str = "15:45"         # force-close everything (0DTE must not pin)
    poll_seconds: int = 30

    # Broker: "dryrun" logs orders without keys; "alpaca" = free paper trading;
    # "tradier" uses Tradier (sandbox/paper, now requires a subscription)
    broker: str = "dryrun"
    tradier_env: str = "sandbox"
    # Data: "dryrun" (no data), "alpaca" (free, 15-min delayed + history),
    # "tradier" (delayed unless subscribed). Env DATA_SOURCE overrides.
    data_source: str = "dryrun"
