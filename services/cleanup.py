import logging
from telegram.ext import ContextTypes

from config import INACTIVE_USER_TIMEOUT
from state import users, queue, queue_lock
from utils import utcnow

logger = logging.getLogger("sparktalks")


async def cleanup_stale_queue(context: ContextTypes.DEFAULT_TYPE):
    async with queue_lock:
        stale = []
        for uid in list(queue):
            u = users.get(uid)
            if not u or u.get("is_banned") or u.get("state") != "SEARCHING" or u.get("partner"):
                stale.append(uid)
        for uid in stale:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            if uid in users and users[uid].get("state") == "SEARCHING":
                users[uid]["state"] = "IDLE"
        if stale:
            logger.info(f"Queue cleanup removed {len(stale)} stale entries")


async def cleanup_inactive_users(context: ContextTypes.DEFAULT_TYPE):
    now = utcnow()
    to_remove = []
    for uid, u in list(users.items()):
        last = u.get("last_active")
        if not last:
            continue
        if u.get("state") == "IDLE" and (now - last).total_seconds() > INACTIVE_USER_TIMEOUT:
            to_remove.append(uid)
    for uid in to_remove:
        users.pop(uid, None)
    if to_remove:
        logger.info(f"Cleaned {len(to_remove)} inactive users from memory")
