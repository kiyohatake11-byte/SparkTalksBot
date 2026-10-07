import logging
from telegram.ext import ContextTypes

from config import INACTIVE_USER_TIMEOUT
from state import (
    users, queue, queue_set, queue_lock, last_next_time,
    image_hash_map, image_hash_user_count, link_warn_count,
    flood_buckets, active_voice_rooms,
)
from utils import utcnow

logger = logging.getLogger("sparktalks")


async def cleanup_stale_queue(context: ContextTypes.DEFAULT_TYPE):
    async with queue_lock:
        stale = []
        for uid in list(queue):
            u = users.get(uid)
            if (not u or u.get("is_banned")
                    or u.get("state") != "SEARCHING" or u.get("partner")):
                stale.append(uid)
        for uid in stale:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
            if uid in users and users[uid].get("state") == "SEARCHING":
                users[uid]["state"] = "IDLE"
        if stale:
            logger.info(f"Queue cleanup removed {len(stale)} stale entries")


async def cleanup_inactive_users(context: ContextTypes.DEFAULT_TYPE):
    from database import _pending_writes, _do_save
    now = utcnow()
    to_remove = []
    for uid, u in list(users.items()):
        last = u.get("last_active")
        if not last:
            continue
        st = u.get("state")
        if st == "IDLE" and (now - last).total_seconds() > INACTIVE_USER_TIMEOUT:
            to_remove.append(uid)
        elif st == "SEARCHING" and uid not in queue_set:
            if (now - last).total_seconds() > 300:
                to_remove.append(uid)

    for uid in to_remove:
        pending = _pending_writes.pop(uid, None)
        if pending:
            try:
                await _do_save(uid, pending)
            except Exception as e:
                logger.warning(f"Flush-before-evict failed for {uid}: {e}")
        users.pop(uid, None)

    if to_remove:
        logger.info(f"Cleaned {len(to_remove)} inactive users from memory")


async def cleanup_last_next(context: ContextTypes.DEFAULT_TYPE):
    now_ts = utcnow().timestamp()
    stale = [uid for uid, ts in last_next_time.items() if now_ts - ts > 3600]
    for uid in stale:
        last_next_time.pop(uid, None)
    if stale:
        logger.debug(f"Cleaned {len(stale)} last_next_time entries")


async def cleanup_image_hashes(context: ContextTypes.DEFAULT_TYPE):
    """Trim image hash maps and reset per-user counts periodically."""
    if len(image_hash_map) > 5000:
        for _ in range(len(image_hash_map) - 5000):
            image_hash_map.pop(next(iter(image_hash_map)), None)
    # Reset user-counts older than 1 hour
    now_ts = utcnow().timestamp()
    stale_uids = []
    for uid, c in image_hash_user_count.items():
        # Simple reset every cycle
        stale_uids.append(uid)
    for uid in stale_uids[:200]:
        image_hash_user_count[uid] = max(0, image_hash_user_count[uid] - 1)
    # Clean stale link warns
    for uid in list(link_warn_count.keys())[:200]:
        link_warn_count[uid] = max(0, link_warn_count[uid] - 1)
    # Clean flood buckets
    for uid in list(flood_buckets.keys())[:500]:
        b = flood_buckets[uid]
        while b and now_ts - b[0] > 60:
            b.popleft()
        if not b:
            flood_buckets.pop(uid, None)

    # Clean voice rooms
    from services.voice_rooms import cleanup_voice_rooms
    await cleanup_voice_rooms(context)