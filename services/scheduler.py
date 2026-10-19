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
                "media": doc.get("media"),
                "created_by": doc.get("created_by"),
            })
        logger.info(f"✅ Loaded {len(scheduled_broadcasts)} scheduled broadcasts")
    except Exception as e:
        logger.error(f"Scheduler load failed: {e}")


async def schedule_broadcast(run_at, message: str = None,
                             media: dict = None, created_by: int = None) -> str:
    if scheduled_collection is None:
        return None
    doc = {
        "run_at": run_at,
        "message": message,
        "media": media,
        "created_by": created_by,
        "sent": False,
        "created_at": utcnow(),
    }
    try:
        result = await scheduled_collection.insert_one(doc)
        sid = str(result.inserted_id)
        scheduled_broadcasts.append({
            "id": sid, "run_at": run_at, "message": message,
            "media": media, "created_by": created_by,
        })
        return sid
    except Exception as e:
        logger.error(f"schedule_broadcast DB insert failed: {e}")
        return None


async def cancel_scheduled(sid: str) -> bool:
    """Cancel — clears from memory ALWAYS, best-effort DB update."""
    from bson import ObjectId
    db_ok = True
    if scheduled_collection is not None:
        try:
            await scheduled_collection.update_one(
                {"_id": ObjectId(sid)},
                {"$set": {"sent": True, "cancelled": True}},
            )
        except Exception as e:
            logger.warning(f"cancel_scheduled DB update failed for {sid}: {e}")
            db_ok = False

    removed = False
    for i, s in enumerate(scheduled_broadcasts):
        if s["id"] == sid:
            scheduled_broadcasts.pop(i)
            removed = True
            break
    return removed or db_ok


async def check_scheduled_broadcasts(context: ContextTypes.DEFAULT_TYPE):
    if not scheduled_broadcasts:
        return
    now = utcnow()
    due = [s for s in scheduled_broadcasts if s["run_at"] <= now]

    for s in due:
        success = False
        error_msg = None
        try:
            if s.get("media"):
                from services.broadcast import execute_media_broadcast
                await execute_media_broadcast(
                    context, s["media"]["chat_id"], s["media"]["message_id"]
                )
            elif s.get("message"):
                from services.broadcast import execute_broadcast
                await execute_broadcast(context, s["message"])
            success = True
        except Exception as e:
            error_msg = str(e)[:200]
            logger.error(f"Scheduled broadcast {s['id']} failed: {e}")

        # ⭐ ALWAYS mark sent=True in DB (success OR failure) — prevents infinite retry on restart
        try:
            from bson import ObjectId
            if scheduled_collection is not None:
                update = {"$set": {"sent": True, "sent_at": now}}
                if not success:
                    update["$set"]["failed"] = True
                    update["$set"]["failed_reason"] = error_msg
                    update["$set"]["failed_at"] = now
                await scheduled_collection.update_one(
                    {"_id": ObjectId(s["id"])}, update,
                )
        except Exception as e:
            logger.error(f"Could not mark broadcast {s['id']} as processed: {e}")

        if s in scheduled_broadcasts:
            scheduled_broadcasts.remove(s)

        if success:
            logger.info(f"✅ Sent scheduled broadcast {s['id']}")
        else:
            logger.warning(f"⚠️ Scheduled broadcast {s['id']} failed permanently: {error_msg}")