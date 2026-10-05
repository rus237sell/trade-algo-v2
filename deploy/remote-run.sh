#!/bin/bash
# Remote runner: polls the private droplet-ops repo for commands from Muse,
# executes them as root, and posts results back.
#
# One-time droplet setup (as root):
#   1. GitHub -> Settings -> Developer settings -> Personal access tokens ->
#      Fine-grained tokens -> Generate new token:
#        - Repository access: Only select repositories -> droplet-ops
#        - Permissions: Contents -> Read and write
#   2. git clone https://x-access-token:<TOKEN>@github.com/rus237sell/droplet-ops.git ~/droplet-ops
#   3. cd ~/trade-algo-v2 && git checkout main && git pull && chmod +x deploy/remote-run.sh
#   4. (crontab -l 2>/dev/null; echo '* * * * * /root/trade-algo-v2/deploy/remote-run.sh >> /root/trade-algo-v2/remote-run.log 2>&1') | crontab -
#
# Notes:
# - Commands run as root with a 120 s timeout. Output capped at ~20 KB.
# - Secret-looking strings (tokens, keys) are redacted before results are posted.
# - A command runs once: only when pending.json's id advances past ~/.remote-run-cursor.
set -u
OPS="$HOME/droplet-ops"
CURSOR="$HOME/.remote-run-cursor"
LOCK="$HOME/.remote-run.lock"
BRANCH="main"

[ -d "$OPS/.git" ] || exit 0
exec 9>"$LOCK" 2>/dev/null || exit 0
flock -n 9 || exit 0   # skip if a previous run is still going
cd "$OPS" || exit 0

# Publish any unpushed results, then sync with the command inbox.
git pull -q --rebase origin "$BRANCH" 2>/dev/null || true
git push -q origin "$BRANCH" 2>/dev/null || true
git fetch -q origin 2>/dev/null || exit 0

PENDING=$(git show "origin/$BRANCH:pending.json" 2>/dev/null) || exit 0
ID=$(printf '%s' "$PENDING" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id', 0))" 2>/dev/null) || exit 0
case "$ID" in ''|*[!0-9]*) exit 0;; esac
[ "$ID" -gt 0 ] 2>/dev/null || exit 0
LAST=$(cat "$CURSOR" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0;; esac
[ "$ID" -gt "$LAST" ] 2>/dev/null || exit 0

CMD=$(printf '%s' "$PENDING" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cmd',''))")
if [ -z "$CMD" ]; then echo "$ID" > "$CURSOR"; exit 0; fi

# Execute with timeout; capture combined output.
OUT=$(timeout 120 bash -c "$CMD" 2>&1)
CODE=$?
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

git add "results/$ID.json" 2>/dev/null
if git diff --cached --quiet 2>/dev/null; then
    :  # nothing new (shouldn't happen)
else
    git -c user.name="droplet-runner" -c user.email="runner@localhost" commit -qm "result $ID (exit $CODE)"
    git push -q origin "$BRANCH" 2>/dev/null || true
fi
echo "$ID" > "$CURSOR"
