#!/usr/bin/env bash
# Nightly backup of the chatbot stack.
#
# Produces three artefacts under /var/backups/chatbot/<YYYY-MM-DD>/:
#   - mongo.gz    — mongodump archive of chatbot_db
#   - chroma.tgz  — tar of the chromadb container's data volume
#   - manifest.json — sizes + sha256s + run timestamp
#
# Retention: 7 daily backups + 4 weekly (Sundays) + 3 monthly (1st of month).
# Older backups are pruned at the end of each run so the backup root never
# grows unboundedly.
#
# Wire into a host cron with:
#   0 2 * * *  /home/administrator/Project\ pfe/ai-chatbot-platform-1/scripts/backup.sh >> /var/log/chatbot-backup.log 2>&1
set -euo pipefail

PROJECT_DIR="/home/administrator/Project pfe/ai-chatbot-platform-1"
BACKUP_ROOT="${BACKUP_ROOT:-/home/administrator/backups/chatbot}"
DATE="$(date -u +%F)"
TARGET="$BACKUP_ROOT/$DATE"
mkdir -p "$TARGET"

cd "$PROJECT_DIR"

# ── Load Mongo password from .env (won't print to log) ──
MONGO_PASSWORD="$(grep -E '^MONGO_PASSWORD=' .env | cut -d= -f2-)"
if [[ -z "${MONGO_PASSWORD}" ]]; then
  echo "[$(date -u +%FT%TZ)] ERROR: MONGO_PASSWORD missing from .env" >&2
  exit 1
fi

# ── 1. Mongo dump ──
echo "[$(date -u +%FT%TZ)] mongodump → $TARGET/mongo.gz"
docker exec chatbot-mongodb sh -c \
  "mongodump --uri='mongodb://admin:${MONGO_PASSWORD}@127.0.0.1:27017/?authSource=admin' \
             --db=chatbot_db --gzip --archive" \
  > "$TARGET/mongo.gz"

# ── 2. Chroma volume snapshot ──
echo "[$(date -u +%FT%TZ)] chroma tar → $TARGET/chroma.tgz"
docker run --rm \
  -v ai-chatbot-platform-1_chroma_data:/data:ro \
  -v "$TARGET":/out alpine:3 \
  tar czf /out/chroma.tgz -C /data .

# ── 2b. Sanity: a healthy chroma volume is multi-MB. A tiny tar means the
#        volume is empty / mounted at the wrong path (the bug fixed 2026-06-22).
#        Warn loudly so a silent data-loss regression can't pass unnoticed.
CHROMA_MIN_BYTES="${CHROMA_MIN_BYTES:-51200}"  # 50 KB floor
SIZE_CHROMA_CHECK=$(stat -c%s "$TARGET/chroma.tgz")
if [[ "$SIZE_CHROMA_CHECK" -lt "$CHROMA_MIN_BYTES" ]]; then
  echo "[$(date -u +%FT%TZ)] WARNING: chroma.tgz is only ${SIZE_CHROMA_CHECK} bytes (< ${CHROMA_MIN_BYTES}). The chroma volume may be empty or mis-mounted — vector store NOT backed up!" >&2
fi

# ── 3. Manifest ──
SHA_MONGO=$(sha256sum "$TARGET/mongo.gz" | awk '{print $1}')
SHA_CHROMA=$(sha256sum "$TARGET/chroma.tgz" | awk '{print $1}')
SIZE_MONGO=$(stat -c%s "$TARGET/mongo.gz")
SIZE_CHROMA=$(stat -c%s "$TARGET/chroma.tgz")
cat > "$TARGET/manifest.json" <<EOF
{
  "date": "$DATE",
  "created_at": "$(date -u +%FT%TZ)",
  "mongo": {"size": $SIZE_MONGO, "sha256": "$SHA_MONGO"},
  "chroma": {"size": $SIZE_CHROMA, "sha256": "$SHA_CHROMA"}
}
EOF

# ── 4. Retention: keep 7 daily + last 4 Sundays + last 3 first-of-months ──
keep_dates() {
  ls -1 "$BACKUP_ROOT" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort -r > /tmp/.bk_all
  # 7 latest daily
  head -n 7 /tmp/.bk_all
  # last 4 Sundays
  while read -r d; do
    [[ -z "$d" ]] && continue
    if [[ "$(date -d "$d" +%u 2>/dev/null)" == "7" ]]; then echo "$d"; fi
  done < /tmp/.bk_all | head -n 4
  # last 3 first-of-month
  awk '/-01$/' /tmp/.bk_all | head -n 3
}
KEEP=$(keep_dates | sort -u)

while read -r d; do
  [[ -z "$d" ]] && continue
  if ! grep -q "^$d$" <<< "$KEEP"; then
    echo "[$(date -u +%FT%TZ)] pruning $BACKUP_ROOT/$d"
    rm -rf "$BACKUP_ROOT/$d"
  fi
done < /tmp/.bk_all

echo "[$(date -u +%FT%TZ)] backup complete: $TARGET (mongo $((SIZE_MONGO/1024))k, chroma $((SIZE_CHROMA/1024))k)"
