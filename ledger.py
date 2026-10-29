"""Ledger summary: expectancy and win-rate stats from trades.jsonl.

The v2.7 ledger records estimated P&L per exit, but status.py only
showed today's count and net. A day count says nothing about whether
the system has an edge — that needs expectancy (net per exit), win
rate, avg win vs avg loss, and profit factor, cut by exit reason and
by symbol so a losing exit path or symbol is visible instead of being
averaged away.

v2.10 adds the risk path behind those averages: max and current
drawdown of the cumulative P&L curve (in ledger order) and win/loss
streaks. Expectancy alone hides sequence — the same net can arrive
via a shallow grind or a deep hole the daily guard would not
tolerate, and drawdown is the number that tells them apart.

v2.11 adds the calendar cut: the same stats per trading day, plus
day-level aggregates (winning/losing days, best/worst day, average
day, and how many days finished at or beyond the daily guard's
+$500 target / -$1,000 loss limit). The bot's risk is managed per
day — the guard resets every morning — so an all-time expectancy
that mixes a few limit days with many quiet days hides the shape
the guard actually sees. A day's final net at/past a limit is
counted as a guard day; the intraday path can touch a limit earlier
than the close, so this is a floor, not an exact halt count.

Pure functions, no network and no file IO in the math: callers pass
parsed ledger records in. Reporting only — nothing here changes
trading, sizing, or guard behavior. Estimates, not broker fills;
reconcile.py remains the fill-based check.
"""

# DailyGuard defaults from config.py, mirrored here so this module
# stays import-free; summarize() callers can override both.
DEFAULT_GUARD_TARGET = 500.0
DEFAULT_GUARD_MAX_LOSS = -1000.0


def _path_stats(pnls):
    """Drawdown and streak stats for exit P&Ls in chronological order.

    Drawdowns are positive dollar magnitudes: max_drawdown is the
    largest peak-to-trough fall of the cumulative curve (0.0 when the
    curve never falls below a prior peak), current_drawdown is how
    far the last point sits below the running peak. A breakeven exit
    breaks both streaks. current_streak is signed: +n consecutive
    wins, -n consecutive losses, 0 after a breakeven or no exits.
    """
    peak = cum = 0.0
    max_dd = 0.0
    longest_win = longest_loss = 0
    run_win = run_loss = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if p > 0:
            run_win, run_loss = run_win + 1, 0
        elif p < 0:
            run_win, run_loss = 0, run_loss + 1
        else:
            run_win = run_loss = 0
        longest_win = max(longest_win, run_win)
        longest_loss = max(longest_loss, run_loss)
    if run_win:
        current = run_win
    elif run_loss:
        current = -run_loss
    else:
        current = 0
    return {
        "max_drawdown": round(max_dd, 2),
        "current_drawdown": round(peak - cum, 2) if pnls else 0.0,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak": current,
    }


def _stats(pnls):
    """Core stats for a list of exit P&L values (in ledger order)."""
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)  # negative, or 0.0
    net = gross_profit + gross_loss
    n = len(pnls)
    out = {
        "exits": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": n - len(wins) - len(losses),
        "win_rate": (len(wins) / n) if n else None,
        "net": round(net, 2),
        "expectancy": round(net / n, 2) if n else None,
        "avg_win": round(gross_profit / len(wins), 2) if wins else None,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else None,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        # Undefined (None) with no losses or no profit — never divide
        # by zero and never report inf as a real edge.
        "profit_factor": (round(gross_profit / abs(gross_loss), 2)
                          if gross_loss < 0 and gross_profit > 0 else None),
        "largest_win": round(max(pnls), 2) if pnls else None,
        "largest_loss": round(min(pnls), 2) if pnls else None,
    }
    out.update(_path_stats(pnls))
    return out


def parse_lines(lines):
    """Parse trades.jsonl lines into records, skipping blank/corrupt lines.

    A truncated final line (bot killed mid-write) must not lose the
    whole summary, so bad JSON is skipped the same way status.py
    already skips it for today's count.
    """
    import json
    records = []
    for line in lines:
        line = str(line).strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _day_key(rec):
    """Trading-day key (YYYY-MM-DD) from a record's `time`, or None.

    Ledger times are ET ISO strings written by bot.py, and status.py
    already matches "today" by string prefix, so the first 10 chars
    are the ET calendar day — no timezone conversion needed. A time
    that does not start with a plausible date yields None rather
    than a garbage bucket.
    """
    t = str(rec.get("time") or "")
    if (len(t) >= 10 and t[4] == "-" and t[7] == "-"
            and t[:4].isdigit() and t[5:7].isdigit()
            and t[8:10].isdigit()):
        return t[:10]
    return None


def _day_aggregates(by_day, guard_target, guard_max_loss):
    """Day-level aggregates over the per-day stats in `by_day`.

    best_day / worst_day are {"date", "net"} dicts (earliest date
    wins a tie) or None when there are no trading days. A day counts
    toward target_days / loss_limit_days when its FINAL net reaches
    the guard limit — a floor on true halts, as documented above.
    """
    days = sorted(by_day.items())  # by date, earliest first
    nets = [(d, v["net"]) for d, v in days]
    winning = [n for _, n in nets if n > 0]
    losing = [n for _, n in nets if n < 0]
    total = len(nets)
    best = worst = None
    for d, n in nets:
        if best is None or n > best["net"]:
            best = {"date": d, "net": n}
        if worst is None or n < worst["net"]:
            worst = {"date": d, "net": n}
    return {
        "trading_days": total,
        "winning_days": len(winning),
        "losing_days": len(losing),
        "breakeven_days": total - len(winning) - len(losing),
        "win_day_rate": (len(winning) / total) if total else None,
        "avg_day_net": (round(sum(n for _, n in nets) / total, 2)
                        if total else None),
        "best_day": best,
        "worst_day": worst,
        "target_days": sum(1 for _, n in nets if n >= guard_target),
        "loss_limit_days": sum(1 for _, n in nets if n <= guard_max_loss),
    }


def summarize(records, guard_target=DEFAULT_GUARD_TARGET,
              guard_max_loss=DEFAULT_GUARD_MAX_LOSS):
    """Summarize ledger records (entries + exits) into stats.

    Only exit records with a numeric est_pnl count toward P&L stats;
    exits missing est_pnl are counted separately as `unpriced_exits`
    so a gap in the ledger is visible rather than silently treated
    as a $0 trade. Returns the overall stats (including drawdown and
    streak stats from _path_stats) plus `by_reason`, `by_symbol`,
    and `by_day` breakdowns (same stat shape; `by_day` values also
    carry that day's `entries`), `entries`, `recovered_exits`, and
    `days` (day-level aggregates from _day_aggregates, judged
    against guard_target / guard_max_loss).

    A priced exit whose `time` has no usable date still counts in
    every non-day stat but lands in no day bucket; those are counted
    as `undated_exits` so the gap is visible. Days with entries but
    no priced exit are not trading days here — they have no P&L to
    judge against the guard.
    """
    entries = 0
    unpriced_exits = 0
    recovered_exits = 0
    undated_exits = 0
    all_pnls = []
    by_reason = {}
    by_symbol = {}
    day_pnls = {}    # date -> [exit P&Ls in ledger order]
    day_entries = {}  # date -> entry count
    for rec in records or []:
        event = rec.get("event")
        if event == "entry":
            entries += 1
            day = _day_key(rec)
            if day is not None:
                day_entries[day] = day_entries.get(day, 0) + 1
            continue
        if event != "exit":
            continue
        raw = rec.get("est_pnl")
        try:
            pnl = float(raw)
        except (TypeError, ValueError):
            unpriced_exits += 1
            continue
        all_pnls.append(pnl)
        if rec.get("recovered"):
            recovered_exits += 1
        by_reason.setdefault(rec.get("reason") or "?", []).append(pnl)
        by_symbol.setdefault(rec.get("symbol") or "?", []).append(pnl)
        day = _day_key(rec)
        if day is None:
            undated_exits += 1
        else:
            day_pnls.setdefault(day, []).append(pnl)
    out = _stats(all_pnls)
    out["entries"] = entries
    out["unpriced_exits"] = unpriced_exits
    out["recovered_exits"] = recovered_exits
    out["undated_exits"] = undated_exits
    out["by_reason"] = {k: _stats(v) for k, v in sorted(by_reason.items())}
    out["by_symbol"] = {k: _stats(v) for k, v in sorted(by_symbol.items())}
    by_day = {}
    for day in sorted(day_pnls):
        stats = _stats(day_pnls[day])
        stats["entries"] = day_entries.get(day, 0)
        by_day[day] = stats
    out["by_day"] = by_day
    out["days"] = _day_aggregates(by_day, guard_target, guard_max_loss)
    return out
