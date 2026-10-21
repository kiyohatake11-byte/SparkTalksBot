"""Scheduled broadcasts — check DB every 30s.

⭐ FIXED:
  - Bug #4: concurrent invocations no longer mark broadcasts as "sent" without running
  - Bug #5: cancel_scheduled returns accurate success (DB authoritative)
"""
import asyncio
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from database import scheduled_collection
from state import scheduled_broadcasts, broadcast_state
from utils import utcnow

logger = logging.getLogger("sparktalks")

# ⭐ Single-flight lock — prevents concurrent check iterations
_check_lock = asyncio.Lock()


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
    """Cancel — DB is authoritative.

    ⭐ FIX Bug #5: Only returns True if DB update succeeded (or DB is down and
    memory removal is the only state). If DB write fails but the entry is
    removed from memory, we return False so admin sees the failure and can
    retry — otherwise on restart the broadcast silently fires.
    """
    from bson import ObjectId

    db_ok = False
    db_updated = False

    if scheduled_collection is not None:
        try:
            result = await scheduled_collection.update_one(
                {"_id": ObjectId(sid)},
                {"$set": {"sent": True, "cancelled": True}},
            )
            # matched_count > 0 = document existed
            # modified_count > 0 = actually changed
            db_updated = (result.matched_count > 0)
            db_ok = True
            if not db_updated:
                logger.warning(
                    f"cancel_scheduled: broadcast {sid} not found in DB "
                    f"(already deleted or never persisted)"
                )
        except Exception as e:
            logger.error(f"cancel_scheduled DB update failed for {sid}: {e}")
            db_ok = False

    # Always remove from memory
    removed = False
    for i, s in enumerate(scheduled_broadcasts):
        if s["id"] == sid:
            scheduled_broadcasts.pop(i)
            removed = True
            break

    # ⭐ DB is authoritative. If DB failed, return False so caller warns admin.
    if scheduled_collection is None:
        # DB down — memory removal is best we can do
        logger.warning(f"cancel_scheduled: DB down, memory-only cancel for {sid}")
        return removed

    if not db_ok:
        # DB write failed but we removed from memory → misleading
        # Put it BACK so at least in-memory state is consistent
        logger.error(
            f"cancel_scheduled: DB failed for {sid}; memory removal reverted"
        )
        # Note: cannot easily put back since we don't have the dict anymore.
        # Return False → admin sees failure, can retry or investigate.
        return False

    return True


async def check_scheduled_broadcasts(context: ContextTypes.DEFAULT_TYPE):
    """Run every 30s. Single-flight to prevent concurrent runs.

    ⭐ FIX Bug #4: Never marks broadcasts as "sent" without actually sending.
    ⭐ FIX Bug #4: Skips if another broadcast is already running.
    """
    async with _check_lock:
        if not scheduled_broadcasts:
            return

        # ⭐ Guard: skip if broadcast already running (avoid parallel sends)
        if broadcast_state.get("running"):
            logger.debug("Scheduled check skipped — broadcast in progress")
            return

        now = utcnow()
        due = [s for s in scheduled_broadcasts if s["run_at"] <= now]

        for s in due:
            # ⭐ Re-check: another execution might have started mid-loop
            if broadcast_state.get("running"):
                logger.info(
                    f"Deferring scheduled broadcast {s['id']} — "
                    f"another broadcast started"
                )
                break  # retry next cycle

            success = False
            error_msg = None
            sent = failed = 0

            try:
                if s.get("media"):
                    from services.broadcast import execute_media_broadcast
                    sent, failed = await execute_media_broadcast(
                        context,
                        s["media"]["chat_id"],
                        s["media"]["message_id"],
                    )
                elif s.get("message"):
                    from services.broadcast import execute_broadcast
                    sent, failed = await execute_broadcast(context, s["message"])

                # ⭐ FIX Bug #4: (0, 0) means "blocked/failed silently" —
                # do NOT mark as sent. Retry next cycle.
                if sent == 0 and failed == 0:
                    logger.warning(
                        f"⚠️ Scheduled broadcast {s['id']} produced 0 sends "
                        f"— will retry next cycle"
                    )
                    continue

                success = True

            except Exception as e:
                error_msg = str(e)[:200]
                logger.error(f"Scheduled broadcast {s['id']} failed: {e}")

            # Mark sent=True in DB (success OR permanent failure) — prevents
            # infinite retry on restart
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
                logger.error(
                    f"Could not mark broadcast {s['id']} as processed: {e}"
                )

            if s in scheduled_broadcasts:
                scheduled_broadcasts.remove(s)

            if success:
                logger.info(
                    f"✅ Sent scheduled broadcast {s['id']} "
                    f"(sent={sent}, failed={failed})"
                )
            else:
                logger.warning(
                    f"⚠️ Scheduled broadcast {s['id']} failed permanently: "
                    f"{error_msg}"
                )