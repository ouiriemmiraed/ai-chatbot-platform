#!/usr/bin/env bash
# Restore drill — verifies the latest nightly backup is actually usable.
#
# Spins up a throw-away Mongo container, restores the latest mongo.gz into
# it, and runs a tiny smoke query. Exits non-zero (with a logged reason)
# if any step fails. Run monthly via cron; alert on non-zero exit.
#
# Wire into host cron:
#   0 4 1 * *  /home/administrator/Project\ pfe/ai-chatbot-platform-1/scripts/restore_drill.sh >> /var/log/chatbot-restore-drill.log 2>&1
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/home/administrator/backups/chatbot}"
LATEST=$(ls -1 "$BACKUP_ROOT" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort -r | head -n 1)
if [[ -z "$LATEST" ]]; then
  echo "[$(date -u +%FT%TZ)] FAIL: no backup found in $BACKUP_ROOT" >&2
  exit 1
fi
TARGET="$BACKUP_ROOT/$LATEST"
echo "[$(date -u +%FT%TZ)] drill: restoring $TARGET"

CONTAINER="chatbot-restore-drill-$$"
TEST_PWD="restore-drill-pwd"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# ── 1. Boot a clean Mongo ──
docker run -d --name "$CONTAINER" --rm \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD="$TEST_PWD" \
  -p 127.0.0.1:0:27017 \
  mongo:7 > /dev/null

# Wait until ready
for i in $(seq 1 30); do
  if docker exec "$CONTAINER" mongosh -u admin -p "$TEST_PWD" --quiet \
        --authenticationDatabase admin --eval 'db.runCommand({ping: 1}).ok' 2>/dev/null \
        | grep -q '^1$'; then break; fi
  sleep 1
done

# ── 2. Restore the dump ──
cat "$TARGET/mongo.gz" | docker exec -i "$CONTAINER" \
  mongorestore --uri="mongodb://admin:${TEST_PWD}@127.0.0.1:27017/?authSource=admin" \
               --gzip --archive --drop

# ── 3. Smoke query: count documents in expected collections ──
SMOKE=$(docker exec "$CONTAINER" mongosh -u admin -p "$TEST_PWD" \
  --authenticationDatabase admin --quiet chatbot_db --eval '
  var c = ["users","chatbot_configs","conversations","scraped_pages"];
  var ok = true;
  c.forEach(function(n) {
    var n2 = db[n].countDocuments({});
    print(n + " " + n2);
  });
')

echo "$SMOKE"

# ── 3b. Validate the Chroma archive too — the mongo restore alone would have
#        missed the empty-volume bug (chroma backup was 86 bytes for weeks).
#        Extract chroma.tgz and assert it carries a non-trivial chroma.sqlite3.
CHROMA_OK=0
CHROMA_TGZ="$TARGET/chroma.tgz"
if [[ -f "$CHROMA_TGZ" ]]; then
  SQLITE_BYTES=$(tar tzvf "$CHROMA_TGZ" 2>/dev/null | awk '/chroma\.sqlite3$/ {print $3; exit}')
  SQLITE_BYTES="${SQLITE_BYTES:-0}"
  if [[ "$SQLITE_BYTES" -ge 100000 ]]; then
    echo "[$(date -u +%FT%TZ)] chroma archive OK: chroma.sqlite3 = ${SQLITE_BYTES} bytes"
    CHROMA_OK=1
  else
    echo "[$(date -u +%FT%TZ)] chroma archive SUSPECT: chroma.sqlite3 = ${SQLITE_BYTES} bytes (< 100000) — vector store may not be backed up" >&2
  fi
else
  echo "[$(date -u +%FT%TZ)] chroma archive MISSING: $CHROMA_TGZ not found" >&2
fi

# ── 4. Pass/fail rule: chatbot_configs must restore AND chroma must be valid. ──
N_USERS=$(echo "$SMOKE" | awk '/^users / {print $2}')
N_CONFIGS=$(echo "$SMOKE" | awk '/^chatbot_configs / {print $2}')

if [[ "${N_CONFIGS:-0}" -ge 1 && "$CHROMA_OK" -eq 1 ]]; then
  echo "[$(date -u +%FT%TZ)] drill OK: $N_USERS users, $N_CONFIGS chatbot_configs, chroma valid"
  exit 0
fi

echo "[$(date -u +%FT%TZ)] drill FAIL: mongo configs=${N_CONFIGS:-0}, chroma_ok=${CHROMA_OK}" >&2
exit 2
