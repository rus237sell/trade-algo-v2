#!/bin/bash
# Remote runner v2: polls the private droplet-ops repo for commands from Muse,
# executes them as root, and posts results back.
#
# Robustness: verbose logging, mkdir-based locking (self-healing), timeouts
# on all network operations. Failures are LOUD (logged), never silent.
#
# Cron: * * * * * /root/trade-algo-v2/deploy/remote-run.sh >> /root/trade-algo-v2/remote-run.log 2>&1
set -u
OPS="$HOME/droplet-ops"
CURSOR="$HOME/.remote-run-cursor"
LOCKDIR="$HOME/.remote-run.lockdir"
BRANCH="main"
LOG="$HOME/trade-algo-v2/remote-run.log"

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }

# --- lock (mkdir is atomic; stale locks self-heal after 10 min) ---
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    LOCK_AGE=$(($(date +%s) - $(stat -c %Y "$LOCKDIR" 2>/dev/null || echo 0)))
    if [ "$LOCK_AGE" -gt 600 ]; then
        log "WARN: breaking stale lock (age ${LOCK_AGE}s)"
        rm -rf "$LOCKDIR"
        mkdir "$LOCKDIR" 2>/dev/null || { log "ERROR: cannot create lockdir"; exit 1; }
    else
        exit 0  # another instance is running
    fi
fi
trap 'rm -rf "$LOCKDIR"' EXIT

[ -d "$OPS/.git" ] || { log "ERROR: $OPS not a git repo"; exit 1; }
cd "$OPS" || { log "ERROR: cannot cd $OPS"; exit 1; }

# --- sync (all network ops have timeouts) ---
if ! timeout 60 git pull -q --rebase origin "$BRANCH" 2>>"$LOG"; then
    log "WARN: git pull --rebase failed (continuing)"
fi
timeout 60 git push -q origin "$BRANCH" 2>>"$LOG" || log "WARN: git push failed (will retry)"
if ! timeout 60 git fetch -q origin 2>>"$LOG"; then
    log "ERROR: git fetch failed; exiting"
    exit 1
fi

# --- check for new command ---
PENDING=$(timeout 30 git show "origin/$BRANCH:pending.json" 2>>"$LOG") || { log "ERROR: cannot read pending.json"; exit 1; }
ID=$(printf '%s' "$PENDING" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id', 0))" 2>>"$LOG") || { log "ERROR: cannot parse pending.json"; exit 1; }
case "$ID" in ''|*[!0-9]*) log "ERROR: bad id [$ID]"; exit 1;; esac
[ "$ID" -gt 0 ] || exit 0
LAST=$(cat "$CURSOR" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0;; esac
if ! [ "$ID" -gt "$LAST" ] 2>/dev/null; then exit 0; fi
log "CMD $ID: new command (cursor $LAST)"

CMD=$(printf '%s' "$PENDING" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cmd',''))")
if [ -z "$CMD" ]; then echo "$ID" > "$CURSOR"; exit 0; fi

# --- execute with timeout; capture combined output ---
log "CMD $ID: executing ($(printf '%s' "$CMD" | head -c 80)…)"
OUT=$(timeout 120 bash -c "$CMD" 2>&1)
CODE=$?
log "CMD $ID: exit=$CODE, $(printf '%s' "$OUT" | wc -c) bytes"
# Redact secret-looking material before posting.
OUT=$(printf '%s' "$OUT" | sed -E -e 's/(github_pat_|ghp_|gho_|ghu_)[A-Za-z0-9_]+/\1[REDACTED]/g' -e 's/AKIA[0-9A-Z]{16}/AKIA[REDACTED]/g' | head -c 20000)

export RR_ID="$ID" RR_CODE="$CODE" RR_OUT="$OUT"
mkdir -p results
python3 - <<'PYEOF' > "results/$ID.json"
import json, os, datetime
print(json.dumps({
    "id": int(os.environ["RR_ID"]),
    "exit": int(os.environ["RR_CODE"]),
    "finished": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "output": os.environ.get("RR_OUT", ""),
}))
PYEOF

git add "results/$ID.json" 2>>"$LOG"
if git diff --cached --quiet 2>>"$LOG"; then
    log "CMD $ID: nothing to commit (unexpected)"
else
    git -c user.name="droplet-runner" -c user.email="runner@localhost" commit -qm "result $ID (exit $CODE)" 2>>"$LOG"
    timeout 60 git push -q origin "$BRANCH" 2>>"$LOG" || log "WARN: result push failed (will retry next run)"
fi
echo "$ID" > "$CURSOR"
log "CMD $ID: done"
