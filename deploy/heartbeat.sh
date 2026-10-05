#!/bin/bash
# Heartbeat: push the bot's state.json to the live-status branch.
# Droplet cron (as root): */10 13-20 * * 1-5 /root/trade-algo-v2/deploy/heartbeat.sh >> /root/trade-algo-v2/heartbeat.log 2>&1
# Requires: git credential helper configured with a token that has
# contents:write on this repo (see README "Monitoring").
# NOTE: leaves the droplet checkout on the live-status branch by design,
# so main's history stays clean. Switch back with `git checkout main`.
set -e
cd "$HOME/trade-algo-v2" || exit 0
[ -f state.json ] || exit 0
git checkout -q -B live-status
git add state.json
git diff --cached --quiet && exit 0  # nothing changed
git -c user.name="scalp-city-bot" -c user.email="scalp-city-bot@localhost" \
    commit -qm "heartbeat $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push -q origin live-status
