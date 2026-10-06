#!/bin/bash
# Heartbeat: push the bot's state.json to the live-status branch.
# Uses a dedicated git worktree so the main checkout NEVER changes branch
# (run_live.sh's  keeps working, no branch juggling).
# One-time setup (as root): cd /root/trade-algo-v2 && git worktree add /root/.heartbeat-wt live-status
# Droplet cron (as root): */10 13-20 * * 1-5 /root/trade-algo-v2/deploy/heartbeat.sh >> /root/trade-algo-v2/heartbeat.log 2>&1
# Requires: git credential helper configured with a token that has
# contents:write on this repo.
set -e
SRC="$HOME/trade-algo-v2/state.json"
WT="$HOME/.heartbeat-wt"
[ -f "$SRC" ] || exit 0
[ -e "$WT/.git" ] || { echo "heartbeat worktree missing: run the one-time setup" >&2; exit 1; }
cd "$WT" || exit 1
git fetch -q origin live-status 2>/dev/null || true
git reset -q --hard origin/live-status 2>/dev/null || true
cp "$SRC" state.json
git add state.json
if ! git diff --cached --quiet; then
  git -c user.name="scalp-city-bot" -c user.email="scalp-city-bot@localhost" \
      commit -qm "heartbeat $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push -q origin live-status
fi
