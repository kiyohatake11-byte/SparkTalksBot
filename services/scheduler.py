"""Scheduled broadcasts — check DB every 30s."""
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from database import scheduled_collection
from state import scheduled_broadcasts
from utils import utcnow

logger = logging.getLogger("sparktalks")


async def load_scheduled_from_db():
    """Load pending scheduled broadcasts into memory on startup."""
    if scheduled_collection is None:
        return
    try:
        scheduled_broadcasts.clear()
        cursor = scheduled_collection.find({"sent": False})
        async for doc in cursor:
            scheduled_broadcasts.append({
                "id": str(doc["_id"]),
                "run_at": doc["run_at"],
                "message": doc.get("message"),
                "media": doc.get("media"),  # {chat_id, message_id}
                "created_by": doc.get("created_by"),
            })
        logger.info(f"✅ Loaded {len(scheduled_broadcasts)} scheduled broadcasts")
    except Exception as e:
        logger.error(f"Scheduler load failed: {e}")


async def schedule_broadcast(run_at, message: str = None,
                             media: dict = None, created_by: int = None) -> str:
    """Insert into DB + memory. Returns the id."""
    doc = {
        "run_at": run_at,
        "message": message,
        "media": media,
        "created_by": created_by,
        "sent": False,
        "created_at": utcnow(),
    }
    if scheduled_collection is None:
        return None
    result = await scheduled_collection.insert_one(doc)
    sid = str(result.inserted_id)
    scheduled_broadcasts.append({
        "id": sid, "run_at": run_at, "message": message,
        "media": media, "created_by": created_by,
    })
    return sid


async def cancel_scheduled(sid: str) -> bool:
    from bson import ObjectId
    if scheduled_collection is not None:
        try:
            res = await scheduled_collection.update_one(
                {"_id": ObjectId(sid)}, {"$set": {"sent": True, "cancelled": True}}
            )
        except Exception:
            return False
    for i, s in enumerate(scheduled_broadcasts):
        if s["id"] == sid:
            scheduled_broadcasts.pop(i)
            return True
    return False


async def check_scheduled_broadcasts(context: ContextTypes.DEFAULT_TYPE):
    """Job — runs every SCHEDULED_BROADCAST_CHECK_INTERVAL seconds."""
    if not scheduled_broadcasts:
        return
    now = utcnow()
    due = [s for s in scheduled_broadcasts if s["run_at"] <= now]

    for s in due:
        try:
            if s.get("media"):
                from services.broadcast import execute_media_broadcast
                await execute_media_broadcast(
                    context, s["media"]["chat_id"], s["media"]["message_id"]
                )
            elif s.get("message"):
                from services.broadcast import execute_broadcast
                await execute_broadcast(context, s["message"])

            from bson import ObjectId
            if scheduled_collection is not None:
                await scheduled_collection.update_one(
                    {"_id": ObjectId(s["id"])}, {"$set": {"sent": True, "sent_at": now}}
                )
            scheduled_broadcasts.remove(s)
            logger.info(f"✅ Sent scheduled broadcast {s['id']}")
        except Exception as e:
            logger.error(f"Scheduled broadcast {s['id']} failed: {e}")
            if s in scheduled_broadcasts:
                scheduled_broadcasts.remove(s)