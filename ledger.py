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

Pure functions, no network and no file IO in the math: callers pass
parsed ledger records in. Reporting only — nothing here changes
trading, sizing, or guard behavior. Estimates, not broker fills;
reconcile.py remains the fill-based check.
"""


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


def summarize(records):
    """Summarize ledger records (entries + exits) into stats.

    Only exit records with a numeric est_pnl count toward P&L stats;
    exits missing est_pnl are counted separately as `unpriced_exits`
    so a gap in the ledger is visible rather than silently treated
    as a $0 trade. Returns the overall stats (including drawdown and
    streak stats from _path_stats) plus `by_reason` and
    `by_symbol` breakdowns (same stat shape), `entries`, and
    `recovered_exits` (exits on positions re-adopted after a restart,
    whose P&L is measured from the recovery price, not true entry).
    """
    entries = 0
    unpriced_exits = 0
    recovered_exits = 0
    all_pnls = []
    by_reason = {}
    by_symbol = {}
    for rec in records or []:
        event = rec.get("event")
        if event == "entry":
            entries += 1
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
    out = _stats(all_pnls)
    out["entries"] = entries
    out["unpriced_exits"] = unpriced_exits
    out["recovered_exits"] = recovered_exits
    out["by_reason"] = {k: _stats(v) for k, v in sorted(by_reason.items())}
    out["by_symbol"] = {k: _stats(v) for k, v in sorted(by_symbol.items())}
    return out
