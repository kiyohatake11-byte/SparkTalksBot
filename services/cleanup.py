import logging
from telegram.ext import ContextTypes

from config import (
    INACTIVE_USER_TIMEOUT, IMAGE_HASH_CLEANUP_MAX_AGE,
    DUPLICATE_TEXT_WINDOW, MEDIA_TIMEOUT_SECONDS,
)
from state import (
    users, queue, queue_set, queue_lock, last_next_time,
    image_hash_map, image_hash_user_count, link_warn_count,
    flood_buckets, active_voice_rooms,
    duplicate_text_map, pending_media_timeouts,
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
    from database import _pending_writes, _do_save, _write_lock
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
        pending = None
        async with _write_lock:
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


async def cleanup_image_hashes(context: ContextTypes.DEFAULT_TYPE):
    """Timestamp-based trim of image hash maps."""
    now_ts = utcnow().timestamp()

    # ─── Trim main map by age ──────────────────────────
    stale_hashes = []
    for h, entry in image_hash_map.items():
        created = entry.get("created", 0) if isinstance(entry, dict) else 0
        if now_ts - created > IMAGE_HASH_CLEANUP_MAX_AGE:
            stale_hashes.append(h)
    for h in stale_hashes:
        image_hash_map.pop(h, None)

    # ─── Hard cap ───────────────────────────────────────
    if len(image_hash_map) > 5000:
        for _ in range(len(image_hash_map) - 5000):
            image_hash_map.pop(next(iter(image_hash_map)), None)

    # ─── Trim user counts ──────────────────────────────
    stale_uids = []
    for uid, entry in image_hash_user_count.items():
        last = entry.get("last", 0) if isinstance(entry, dict) else 0
        if now_ts - last > IMAGE_HASH_CLEANUP_MAX_AGE:
            stale_uids.append(uid)
    for uid in stale_uids:
        image_hash_user_count.pop(uid, None)

    # ─── Reset link warns for inactive users ───────────
    stale_link_uids = [uid for uid in link_warn_count
                       if uid not in users]
    for uid in stale_link_uids:
        link_warn_count.pop(uid, None)

    # ─── Trim flood buckets ────────────────────────────
    for uid in list(flood_buckets.keys())[:500]:
        b = flood_buckets[uid]
        while b and now_ts - b[0] > 60:
            b.popleft()
        if not b:
            flood_buckets.pop(uid, None)

    # ─── Voice rooms ───────────────────────────────────
    from services.voice_rooms import cleanup_voice_rooms
    await cleanup_voice_rooms(context)

    # ─── Community check cache ─────────────────────────
    try:
        from state import community_check_cache
        from services.community import COMMUNITY_RECHECK_INTERVAL
        cutoff = now_ts - (COMMUNITY_RECHECK_INTERVAL * 4)
        stale_cuids = [
            k for k, ts in community_check_cache.items()
            if ts < cutoff
        ]
        for k in stale_cuids:
            community_check_cache.pop(k, None)
    except Exception:
        pass


async def cleanup_duplicate_text(context: ContextTypes.DEFAULT_TYPE):
    """Remove stale entries from duplicate-text detection map."""
    now_ts = utcnow().timestamp()
    stale_uids = []
    for uid, text_map in duplicate_text_map.items():
        for text_hash in list(text_map.keys()):
            timestamps = [t for t in text_map[text_hash]
                          if now_ts - t < DUPLICATE_TEXT_WINDOW]
            if not timestamps:
                text_map.pop(text_hash, None)
            else:
                text_map[text_hash] = timestamps
        if not text_map:
            stale_uids.append(uid)
    for uid in stale_uids:
        duplicate_text_map.pop(uid, None)
    if len(duplicate_text_map) > 5000:
        for _ in range(len(duplicate_text_map) - 5000):
            duplicate_text_map.pop(next(iter(duplicate_text_map)), None)


async def cleanup_media_timeouts(context: ContextTypes.DEFAULT_TYPE):
    """Auto-decline media requests older than MEDIA_TIMEOUT_SECONDS."""
    now_ts = utcnow().timestamp()
    stale_keys = []
    for key, entry in list(pending_media_timeouts.items()):
        if now_ts - entry["created"] >= MEDIA_TIMEOUT_SECONDS:
            stale_keys.append(key)

    for key in stale_keys:
        entry = pending_media_timeouts.pop(key, None)
        if not entry:
            continue
        sender_id = key[0]
        msg_id = key[1]
        prompt_id = entry.get("prompt_msg_id")
        receiver_id = entry.get("partner_id")

        if receiver_id and prompt_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=receiver_id,
                    message_id=prompt_id,
                    text="⏰ <i>Media request expired (no response).</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        try:
            await context.bot.send_message(
                chat_id=sender_id,
                text="⏰ <i>Partner didn't respond to your media in time.</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        r = users.get(receiver_id)
        if r:
            pm = r.get("pending_media") or {}
            pm.pop(msg_id, None)
            r["pending_media"] = pm