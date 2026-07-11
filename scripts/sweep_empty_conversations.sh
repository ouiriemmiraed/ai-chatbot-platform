#!/usr/bin/env bash
# Hygiene sweep: delete "empty" conversation shells.
#
# The chat endpoint creates a conversation document (with messages:[]) up-front
# so it can return the conversation_id in the first SSE frame, then fills the
# messages asynchronously. If the turn is never persisted (client disconnects
# before the first token, or a total provider outage before the graceful
# fallback), the empty shell is left behind and inflates the "total
# conversations" KPI without any outcome/quality row.
#
# This sweep removes conversations that have no messages AND are older than
# GRACE_MINUTES (so we never race an in-flight turn), plus any analytics rows
# orphaned by the delete. Idempotent; safe to run on a short cron.
#
# Cron (every 30 min):
#   */30 * * * * /home/administrator/Project\ pfe/ai-chatbot-platform-1/scripts/sweep_empty_conversations.sh >> /home/administrator/backups/sweep.log 2>&1
set -euo pipefail
GRACE_MINUTES="${GRACE_MINUTES:-15}"

docker exec -e GRACE_MINUTES="$GRACE_MINUTES" -i chatbot-backend python3 - <<'PY'
import os
from datetime import datetime, timedelta
from pymongo import MongoClient
db = MongoClient(os.environ["MONGODB_URL"]).chatbot_db
grace = int(os.environ.get("GRACE_MINUTES", "15"))
cutoff = datetime.utcnow() - timedelta(minutes=grace)

# Empty = no messages array, or an empty one. Only sweep ones created before the
# grace cutoff to avoid deleting a conversation whose first turn is still landing.
q = {
    "$and": [
        {"$or": [{"messages": {"$exists": False}}, {"messages": {"$size": 0}}]},
        {"created_at": {"$lt": cutoff}},
    ]
}
empties = [c["_id"] for c in db.conversations.find(q, {"_id": 1})]
if not empties:
    print(f"[{datetime.utcnow().isoformat()}Z] sweep: 0 empty conversations")
else:
    sids = [str(i) for i in empties]
    db.conversations.delete_many({"_id": {"$in": empties}})
    t = db.conversation_tags.delete_many({"conversation_id": {"$in": empties}}).deleted_count
    qd = db.quality_scores.delete_many({"conversation_id": {"$in": empties}}).deleted_count
    c = db.llm_costs.delete_many({"conversation_id": {"$in": empties}}).deleted_count
    f = db.message_feedback.delete_many({"conversation_id": {"$in": sids}}).deleted_count
    print(f"[{datetime.utcnow().isoformat()}Z] sweep: removed {len(empties)} empty conversations "
          f"(+{t} tags, +{qd} quality, +{c} cost, +{f} feedback)")
PY
