"""Complete user deletion — one-shot command for admins."""
import logging

logger = logging.getLogger("sparktalks")


async def delete_user_completely(uid: int) -> dict:
    """
    Fully delete a user from memory + DB + all caches.
    Returns dict with stats.
    """
    from state import (
        users, queue, queue_set, queue_lock,
        last_next_time, muted_users, saved_partners,
        reconnect_daily, pending_save_requests,
        link_warn_count, flood_buckets,
        image_hash_user_count, duplicate_text_map,
        message_reactions_map, message_edit_map,
        pending_media_timeouts, active_voice_rooms,
    )
    from database import users_collection, _pending_writes

    stats = {
        "was_in_memory": uid in users,
        "was_in_queue": uid in queue_set,
        "was_in_db": False,
        "partner_notified": False,
        "db_deleted": False,
        "memory_cleaned": False,
        "error": None,
    }

    # ─── STEP 1: Disconnect from partner if in chat ───
    u = users.get(uid)
    if u and u.get("partner"):
        try:
            partner_id = u["partner"]
            partner = users.get(partner_id)
            if partner:
                partner["partner"] = None
                partner["state"] = "IDLE"
                partner["pending_media"] = {}
                stats["partner_notified"] = True
        except Exception as e:
            logger.debug(f"Partner cleanup failed: {e}")

    # ─── STEP 2: Remove from queue (with lock) ───
    async with queue_lock:
        try:
            queue.remove(uid)
        except ValueError:
            pass
        queue_set.discard(uid)

    # ─── STEP 3: Clean all memory dicts ───
    users.pop(uid, None)
    last_next_time.pop(uid, None)
    muted_users.pop(uid, None)
    saved_partners.pop(uid, None)
    reconnect_daily.pop(uid, None)
    pending_save_requests.pop(uid, None)
    link_warn_count.pop(uid, None)
    flood_buckets.pop(uid, None)
    image_hash_user_count.pop(uid, None)
    duplicate_text_map.pop(uid, None)

    # ─── STEP 4: Clean message maps ───
    uid_prefix = f"{uid}:"
    for key in list(message_reactions_map.keys()):
        if key.startswith(uid_prefix):
            message_reactions_map.pop(key, None)
        else:
            entry = message_reactions_map.get(key)
            if entry and entry.get("target_chat") == uid:
                message_reactions_map.pop(key, None)

    for key in list(message_edit_map.keys()):
        if key.startswith(uid_prefix):
            message_edit_map.pop(key, None)
        else:
            entry = message_edit_map.get(key)
            if entry and entry.get("target_chat") == uid:
                message_edit_map.pop(key, None)

    # ─── STEP 5: Clean media timeouts ───
    for key in list(pending_media_timeouts.keys()):
        if isinstance(key, tuple) and key[0] == uid:
            pending_media_timeouts.pop(key, None)
        else:
            entry = pending_media_timeouts.get(key)
            if entry and entry.get("partner_id") == uid:
                pending_media_timeouts.pop(key, None)

    # ─── STEP 6: Clean voice rooms ───
    for room_id in list(active_voice_rooms.keys()):
        room = active_voice_rooms.get(room_id)
        if room and (room.get("u1") == uid or room.get("u2") == uid):
            active_voice_rooms.pop(room_id, None)

    # ─── STEP 7: Clear pending writes ───
    _pending_writes.pop(uid, None)
    stats["memory_cleaned"] = True

    # ─── STEP 8: Delete from DB ───
    if users_collection is None:
        stats["error"] = "DB not connected"
        return stats

    try:
        # Check if existed
        existing = await users_collection.find_one({"user_id": uid}, {"_id": 1})
        stats["was_in_db"] = existing is not None

        result = await users_collection.delete_one({"user_id": uid})
        stats["db_deleted"] = result.deleted_count > 0

        logger.info(f"🗑️ User {uid} completely deleted | stats={stats}")
    except Exception as e:
        logger.error(f"DB delete failed for {uid}: {e}", exc_info=True)
        stats["error"] = str(e)

    return stats