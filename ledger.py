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

v2.12 adds the intraday cut: the same stats per ET hour of exit.
A 0DTE scalper lives inside one session — the opening-range hour,
midday, and the run into the 15:45 ET flatten are different regimes
(chasing the open vs fighting theta into the close), and reason /
symbol / day cuts all average them together. An exit timed at
09:xx is a different trade from one at 15:xx even on the same day
and symbol, so expectancy by exit hour shows whether the edge (if
any) is a morning edge, an afternoon edge, or neither.

v2.13 adds the direction cut: the same stats per option side
(CALL vs PUT). Every signal in this system is directional — an
upside VWAP / EMA / opening-range break buys a call, a downside
break buys a put — but reason, symbol, day, and hour cuts all pool
the two directions together. If upside breaks work and downside
breaks do not (or the reverse), the pooled expectancy hides it
exactly where a directional scalper needs to see it. Exit records
have carried `side` since v2.7; this cut finally uses it. Sides
are normalized (case-insensitive, C/P expanded to CALL/PUT) and a
missing or blank side lands in "?" rather than a garbage bucket.

v2.14 adds the signal cut: the same stats per entry signal
(vwap-cross-up/dn, ema50-cross-up/dn, orb-breakout-up /
orb-breakdown-dn). Every earlier cut groups an exit by something
the exit record itself carries — its exit reason, symbol, side,
day, hour — but the question a signal-driven scalper most needs
answered is which of the three triggers produced the trade, and
that lives on the ENTRY record: an exit's `reason` is why it
closed (atr-target / atr-stop / flatten-eod), not what opened it.
summarize() therefore pairs each exit with the entry that opened
it (per symbol, OCC match preferred, FIFO otherwise — the bot
holds at most one position per symbol, so pairing is unambiguous
in a live ledger) and buckets the exit's P&L under that entry's
signal. An exit with no entry ahead of it in the ledger (the
file was rotated, or the entry predates it) lands in no signal
bucket and is counted as `unsignaled_exits`, the same way undated
and untimed exits are flagged for the day/hour cuts; its P&L
still counts in every other stat.

v2.11 adds the calendar cut: the same stats per trading day, plus
day-level aggregates (winning/losing days, best/worst day, average
day, and how many days finished at or beyond the daily guard's
+$500 target / -$1,000 loss limit). The bot's risk is managed per
day — the guard resets every morning — so an all-time expectancy
that mixes a few limit days with many quiet days hides the shape
the guard actually sees. A day's final net at/past a limit is
counted as a guard day; the intraday path can touch a limit earlier
than the close, so this is a floor, not an exact halt count.

v2.15 adds the hold-time cut: the same stats per holding-period
bucket (0-4 / 5-14 / 15-29 / 30-59 / 60+ minutes from entry to
exit), plus overall hold aggregates (mean / median / min / max
minutes over priced exits with a usable hold). Every earlier cut
groups an exit by when or why it closed, or what opened it — none
uses how LONG the position was held, and for a 0DTE scalper that
is a first-order question: theta burns the position while it is
held, so a signal whose winners exit in 3 minutes and whose
losers ride for 40 is a different system from one with the
reverse shape, and the pooled expectancy cannot tell them apart.
Hold time comes from the same entry pairing as by_signal: the
exit's `time` minus its paired entry's `time`. A priced exit with
no paired entry, a missing/unparseable time on either record, or
a negative duration (clock skew or a hand-edited ledger) lands in
no hold bucket and is counted as `unheld_exits`; its P&L still
counts in every other stat.

Pure functions, no network and no file IO in the math: callers pass
parsed ledger records in. Reporting only — nothing here changes
trading, sizing, or guard behavior. Estimates, not broker fills;
reconcile.py remains the fill-based check.
"""

# Hold-time buckets in session order: (label, upper bound in minutes,
# exclusive). Anything at or past the last bound lands in HOLD_OVERFLOW.
HOLD_BUCKETS = (("0-4m", 5.0), ("5-14m", 15.0), ("15-29m", 30.0),
                ("30-59m", 60.0))
HOLD_OVERFLOW = "60m+"

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


def _hour_key(rec):
    """Session-hour key (``"09"`` .. ``"15"``) from a record's `time`.

    Ledger times are ET ISO strings (``2026-10-05T14:32:00-04:00``),
    so characters 11-12 are the ET hour of the exit — no timezone
    conversion needed, matching _day_key's prefix approach. A time
    with no ``T`` separator, a non-numeric hour, an hour outside
    0-23, or no ``:`` after the hour yields None rather than a
    garbage bucket. The key is zero-padded so buckets sort in
    session order.
    """
    t = str(rec.get("time") or "")
    if len(t) < 14 or t[10] not in ("T", " ") or t[13] != ":":
        return None
    hh = t[11:13]
    if not hh.isdigit():
        return None
    hour = int(hh)
    if hour < 0 or hour > 23:
        return None
    return f"{hour:02d}"


def _side_key(rec):
    """Direction key (``CALL`` / ``PUT``) from a record's `side`.

    bot.py writes side as CALL/PUT on both entries and exits
    (recovered positions included), but hand-edited or older ledger
    lines may use lowercase or the OCC-style single letter, so the
    value is upper-cased and C/P are expanded before bucketing.
    Anything else non-empty is kept upper-cased as its own bucket;
    a missing or blank side yields "?", matching by_reason /
    by_symbol's unknown bucket.
    """
    side = str(rec.get("side") or "").strip().upper()
    if not side:
        return "?"
    return {"C": "CALL", "P": "PUT"}.get(side, side)


def _parse_time(rec):
    """Aware datetime from a record's `time`, or None.

    Ledger times are ET ISO strings written by bot.py
    (``2026-10-05T14:32:00-04:00``). A date-only or naive value has
    no usable offset, so it yields None rather than a guess; both
    records of a pair must parse for a hold to be computed.
    """
    from datetime import datetime
    t = str(rec.get("time") or "").strip()
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _hold_key(entry_rec, exit_rec):
    """Hold bucket label for an entry/exit pair, or None.

    Duration is exit time minus entry time in minutes; a missing
    pair, an unparseable time on either side, or a negative
    duration yields None (counted as an unheld exit) rather than
    a garbage bucket.
    """
    if entry_rec is None:
        return None
    start, end = _parse_time(entry_rec), _parse_time(exit_rec)
    if start is None or end is None:
        return None
    minutes = (end - start).total_seconds() / 60.0
    if minutes < 0:
        return None
    for label, bound in HOLD_BUCKETS:
        if minutes < bound:
            return label
    return HOLD_OVERFLOW


def _hold_minutes(entry_rec, exit_rec):
    """Hold duration in minutes for a pair, or None (same rules as _hold_key)."""
    if entry_rec is None:
        return None
    start, end = _parse_time(entry_rec), _parse_time(exit_rec)
    if start is None or end is None:
        return None
    minutes = (end - start).total_seconds() / 60.0
    return minutes if minutes >= 0 else None


def _hold_aggregates(holds):
    """Mean / median / min / max over usable hold durations (minutes)."""
    if not holds:
        return {"timed_exits": 0, "avg_hold_min": None,
                "median_hold_min": None, "min_hold_min": None,
                "max_hold_min": None}
    ordered = sorted(holds)
    n = len(ordered)
    mid = (ordered[n // 2] if n % 2
           else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)
    return {"timed_exits": n,
            "avg_hold_min": round(sum(ordered) / n, 2),
            "median_hold_min": round(mid, 2),
            "min_hold_min": round(ordered[0], 2),
            "max_hold_min": round(ordered[-1], 2)}


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
    `by_side`, `by_signal`, `by_hold`, `by_day`, and `by_hour`
    breakdowns (same
    stat shape; `by_day` values also carry that day's `entries`),
    `entries`, `recovered_exits`, `hold` (hold-time aggregates), and `days` (day-level aggregates
    from _day_aggregates, judged against guard_target /
    guard_max_loss).

    A priced exit whose `time` has no usable date still counts in
    every non-day stat but lands in no day bucket; those are counted
    as `undated_exits` so the gap is visible. Days with entries but
    no priced exit are not trading days here — they have no P&L to
    judge against the guard. Likewise a priced exit with no usable
    hour counts everywhere except `by_hour` and is counted as
    `untimed_exits`; a date-only time is dated but untimed, so the
    two gaps are tracked separately.

    For `by_signal`, each exit is paired with the open entry for
    its symbol (an exact OCC match wins; otherwise the oldest open
    entry for that symbol — the bot holds one position per symbol,
    so this is unambiguous live). The pairing consumes the entry
    even when the exit is unpriced, so a later exit cannot claim
    it. A priced exit with no open entry ahead of it counts
    everywhere except `by_signal` and is counted as
    `unsignaled_exits`; an entry whose `reason` is missing buckets
    its exit under "?".

    `by_hold` reuses that same pairing: a priced exit buckets by
    the minutes between its entry's `time` and its own `time`
    (HOLD_BUCKETS order, then HOLD_OVERFLOW). A priced exit whose
    hold cannot be computed — no paired entry, an unparseable time
    on either record, or a negative duration — counts everywhere
    except `by_hold` and is counted as `unheld_exits`. `hold`
    aggregates the usable durations (mean / median / min / max).
    """
    entries = 0
    unpriced_exits = 0
    recovered_exits = 0
    undated_exits = 0
    untimed_exits = 0
    unsignaled_exits = 0
    unheld_exits = 0
    all_pnls = []
    holds = []
    by_reason = {}
    by_symbol = {}
    by_side = {}
    by_signal = {}
    by_hold = {}
    by_hour = {}
    day_pnls = {}    # date -> [exit P&Ls in ledger order]
    day_entries = {}  # date -> entry count
    open_entries = {}  # symbol -> [entry records in ledger order]
    for rec in records or []:
        event = rec.get("event")
        if event == "entry":
            entries += 1
            open_entries.setdefault(rec.get("symbol") or "?", []).append(rec)
            day = _day_key(rec)
            if day is not None:
                day_entries[day] = day_entries.get(day, 0) + 1
            continue
        if event != "exit":
            continue
        # Pair this exit with its opening entry before the priced
        # check: an unpriced exit still closed the position, so it
        # consumes the entry either way.
        signal = None
        entry_rec = None
        queue = open_entries.get(rec.get("symbol") or "?") or []
        if queue:
            occ = rec.get("occ")
            idx = None
            if occ:
                for i, ent in enumerate(queue):
                    if ent.get("occ") == occ:
                        idx = i
                        break
            entry_rec = queue.pop(idx) if idx is not None else queue.pop(0)
            signal = entry_rec.get("reason") or "?"
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
        by_side.setdefault(_side_key(rec), []).append(pnl)
        if signal is None:
            unsignaled_exits += 1
        else:
            by_signal.setdefault(signal, []).append(pnl)
        hold_key = _hold_key(entry_rec, rec)
        if hold_key is None:
            unheld_exits += 1
        else:
            by_hold.setdefault(hold_key, []).append(pnl)
            holds.append(_hold_minutes(entry_rec, rec))
        hour = _hour_key(rec)
        if hour is None:
            untimed_exits += 1
        else:
            by_hour.setdefault(hour, []).append(pnl)
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
    out["untimed_exits"] = untimed_exits
    out["unsignaled_exits"] = unsignaled_exits
    out["unheld_exits"] = unheld_exits
    out["hold"] = _hold_aggregates(holds)
    out["by_reason"] = {k: _stats(v) for k, v in sorted(by_reason.items())}
    out["by_symbol"] = {k: _stats(v) for k, v in sorted(by_symbol.items())}
    out["by_side"] = {k: _stats(v) for k, v in sorted(by_side.items())}
    out["by_signal"] = {k: _stats(v) for k, v in sorted(by_signal.items())}
    # Hold buckets print in session order, not alphabetical order.
    _hold_order = [label for label, _ in HOLD_BUCKETS] + [HOLD_OVERFLOW]
    out["by_hold"] = {k: _stats(by_hold[k]) for k in _hold_order
                      if k in by_hold}
    out["by_hour"] = {k: _stats(v) for k, v in sorted(by_hour.items())}
    by_day = {}
    for day in sorted(day_pnls):
        stats = _stats(day_pnls[day])
        stats["entries"] = day_entries.get(day, 0)
        by_day[day] = stats
    out["by_day"] = by_day
    out["days"] = _day_aggregates(by_day, guard_target, guard_max_loss)
    return out
