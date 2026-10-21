"""Complete user deletion — one-shot for admins.

⭐ FIXED:
  - Bug #8: $pull refs cleanup moved into background retry path
  - Bug #44: task references kept to prevent GC
"""
import asyncio
import logging

logger = logging.getLogger("sparktalks")

DB_DELETE_TIMEOUT = 10.0
BG_RETRY_DELAYS = [2, 4, 8]

# ⭐ FIX Bug #44: retain background task refs to prevent GC
_bg_tasks: set = set()


def _spawn_bg_delete(uid: int):
    """Spawn background delete task + keep reference."""
    task = asyncio.create_task(_background_db_delete(uid))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def delete_user_completely(uid: int) -> dict:
    """Fully delete a user from memory + DB + all caches."""
    from state import (
        users, queue, queue_set, queue_lock,
        last_next_time, muted_users, saved_partners,
        reconnect_daily, pending_save_requests,
        link_warn_count, flood_buckets,
        image_hash_user_count, duplicate_text_map,
        message_reactions_map, message_edit_map,
        pending_media_timeouts, active_voice_rooms,
        deleted_user_ids, community_check_cache,
    )
    from database import users_collection, _pending_writes

    stats = {
        "was_in_memory": uid in users,
        "was_in_queue": uid in queue_set,
        "was_in_db": False,
        "partner_notified": False,
        "db_deleted": False,
        "memory_cleaned": False,
        "refs_cleaned": 0,
        "error": None,
    }

    deleted_user_ids.add(uid)

    # STEP 1: Partner cleanup
    try:
        u = users.get(uid)
        if u and u.get("partner"):
            partner_id = u["partner"]
            partner = users.get(partner_id)
            if partner:
                partner["partner"] = None
                partner["state"] = "IDLE"
                partner["pending_media"] = {}
                partner["_last_partner_id"] = None
                stats["partner_notified"] = True
    except Exception as e:
        logger.debug(f"Partner cleanup failed: {e}")

    # STEP 2: Queue removal
    try:
        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
    except Exception as e:
        logger.debug(f"Queue removal failed: {e}")

    # STEP 3: Memory dicts cleanup
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
    community_check_cache.pop(uid, None)

    # STEP 4: Message maps cleanup
    uid_prefix = f"{uid}:"
    try:
        to_delete = []
        for k in list(message_reactions_map.keys()):
            if k.startswith(uid_prefix):
                to_delete.append(k); continue
            entry = message_reactions_map.get(k)
            if entry and entry.get("target_chat") == uid:
                to_delete.append(k)
        for k in to_delete:
            message_reactions_map.pop(k, None)
    except Exception as e:
        logger.debug(f"Reaction map cleanup failed: {e}")

    try:
        to_delete = []
        for k in list(message_edit_map.keys()):
            if k.startswith(uid_prefix):
                to_delete.append(k); continue
            entry = message_edit_map.get(k)
            if entry and entry.get("target_chat") == uid:
                to_delete.append(k)
        for k in to_delete:
            message_edit_map.pop(k, None)
    except Exception as e:
        logger.debug(f"Edit map cleanup failed: {e}")

    # STEP 5: Media timeouts cleanup
    try:
        for key in list(pending_media_timeouts.keys()):
            if isinstance(key, tuple) and key[0] == uid:
                pending_media_timeouts.pop(key, None)
            else:
                entry = pending_media_timeouts.get(key)
                if entry and entry.get("partner_id") == uid:
                    pending_media_timeouts.pop(key, None)
    except Exception as e:
        logger.debug(f"Media timeout cleanup failed: {e}")

    # STEP 6: Voice rooms cleanup
    try:
        for room_id in list(active_voice_rooms.keys()):
            room = active_voice_rooms.get(room_id)
            if room and (room.get("u1") == uid or room.get("u2") == uid):
                active_voice_rooms.pop(room_id, None)
    except Exception as e:
        logger.debug(f"Voice room cleanup failed: {e}")

    # STEP 7: Pending writes cleanup
    _pending_writes.pop(uid, None)
    stats["memory_cleaned"] = True

    # STEP 8: Clean OTHER users' references (in memory)
    try:
        refs_cleaned = 0
        from database import save_user_to_db
        for other_uid, other_u in list(users.items()):
            if other_uid == uid:
                continue
            blocked = other_u.get("blocked_users")
            if isinstance(blocked, list) and uid in blocked:
                other_u["blocked_users"] = [b for b in blocked if b != uid]
                refs_cleaned += 1
                await save_user_to_db(other_uid, other_u)
            saved = other_u.get("saved_partner_ids")
            if isinstance(saved, list) and uid in saved:
                other_u["saved_partner_ids"] = [s for s in saved if s != uid]
                refs_cleaned += 1
                await save_user_to_db(other_uid, other_u)
            recent = other_u.get("recent_partners")
            if isinstance(recent, list) and uid in recent:
                other_u["recent_partners"] = [r for r in recent if r != uid]
                refs_cleaned += 1
        stats["refs_cleaned"] = refs_cleaned
    except Exception as e:
        logger.debug(f"In-memory refs cleanup failed: {e}")

    # STEP 9: DB delete
    if users_collection is None:
        stats["error"] = "DB not connected"
        logger.warning(f"⚠️ DB not connected — user {uid} removed from memory only")
        return stats

    try:
        try:
            existing = await asyncio.wait_for(
                users_collection.find_one({"user_id": uid}, {"_id": 1}),
                timeout=DB_DELETE_TIMEOUT,
            )
            stats["was_in_db"] = existing is not None
        except asyncio.TimeoutError:
            stats["was_in_db"] = False
            logger.warning(f"⏱️ find_one timed out for {uid}")

        result = await asyncio.wait_for(
            users_collection.delete_one({"user_id": uid}),
            timeout=DB_DELETE_TIMEOUT,
        )
        stats["db_deleted"] = result.deleted_count > 0
        if not stats["was_in_db"]:
            stats["was_in_db"] = result.deleted_count > 0

        # ⭐ FIX Bug #8: $pull refs immediately (fast) — if this fails,
        # _background_db_delete will retry it later
        try:
            await asyncio.wait_for(
                users_collection.update_many(
                    {"blocked_users": uid},
                    {"$pull": {"blocked_users": uid}},
                ),
                timeout=DB_DELETE_TIMEOUT,
            )
            await asyncio.wait_for(
                users_collection.update_many(
                    {"saved_partner_ids": uid},
                    {"$pull": {"saved_partner_ids": uid}},
                ),
                timeout=DB_DELETE_TIMEOUT,
            )
        except Exception as e:
            logger.debug(f"DB refs $pull failed for {uid}: {e}")

        if result.deleted_count > 0:
            logger.info(f"🗑️ User {uid} fully deleted (memory + DB + refs)")
        else:
            logger.info(f"🗑️ User {uid} not in DB (memory already cleared)")

    except asyncio.TimeoutError:
        stats["error"] = f"DB delete timed out ({DB_DELETE_TIMEOUT}s)"
        logger.error(
            f"⏱️ DB delete TIMED OUT for {uid} — scheduling background retry"
        )
        _spawn_bg_delete(uid)
    except Exception as e:
        stats["error"] = str(e)
        logger.error(f"❌ DB delete failed for {uid}: {e}", exc_info=True)
        _spawn_bg_delete(uid)

    return stats


async def _background_db_delete(uid: int):
    """Background retry for DB deletion.

    ⭐ FIX Bug #8: Now also retries $pull of orphan references.
    """
    from database import users_collection
    if users_collection is None:
        logger.warning(f"BG delete skipped for {uid} — DB not connected")
        return

    for attempt, delay in enumerate(BG_RETRY_DELAYS, start=1):
        try:
            await asyncio.sleep(delay)
            result = await asyncio.wait_for(
                users_collection.delete_one({"user_id": uid}),
                timeout=DB_DELETE_TIMEOUT,
            )
            # ⭐ FIX Bug #8: always retry $pull (this is the part that gets lost on timeout)
            try:
                await asyncio.wait_for(
                    users_collection.update_many(
                        {"blocked_users": uid},
                        {"$pull": {"blocked_users": uid}},
                    ),
                    timeout=DB_DELETE_TIMEOUT,
                )
                await asyncio.wait_for(
                    users_collection.update_many(
                        {"saved_partner_ids": uid},
                        {"$pull": {"saved_partner_ids": uid}},
                    ),
                    timeout=DB_DELETE_TIMEOUT,
                )
            except Exception as pull_err:
                logger.debug(f"BG refs $pull failed for {uid} attempt {attempt}: {pull_err}")
            logger.info(
                f"✅ Background DB delete for {uid} — "
                f"attempt {attempt}, deleted={result.deleted_count}"
            )
            return
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ BG delete attempt {attempt} timed out for {uid}")
        except Exception as e:
            logger.warning(f"❌ BG delete attempt {attempt} failed for {uid}: {e}")

    logger.error(
        f"💀 All background DB delete attempts failed for {uid} "
        f"— manual DB cleanup may be needed"
    )