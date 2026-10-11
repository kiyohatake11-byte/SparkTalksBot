import asyncio
import logging

from config import BROADCAST_SLEEP_SECONDS
from database import users_collection
from utils import safe_send, utcnow
from state import analytics

logger = logging.getLogger("sparktalks")


# Rate limit for admin broadcasts
_BROADCAST_MIN_INTERVAL = 60  # seconds
_last_broadcast_at: dict = {}  # admin_uid -> timestamp


def _check_rate_limit(admin_uid: int) -> int:
    """Return seconds to wait, 0 if allowed."""
    last = _last_broadcast_at.get(admin_uid, 0)
    elapsed = utcnow().timestamp() - last
    if elapsed < _BROADCAST_MIN_INTERVAL:
        return int(_BROADCAST_MIN_INTERVAL - elapsed)
    return 0


def _mark_broadcast(admin_uid: int):
    _last_broadcast_at[admin_uid] = utcnow().timestamp()


async def execute_broadcast(context, message: str, admin_uid: int = None) -> tuple:
    """Send text broadcast to all non-banned users."""
    if admin_uid is not None:
        wait = _check_rate_limit(admin_uid)
        if wait > 0:
            await safe_send(
                context, admin_uid,
                f"⏱ <i>Please wait {wait}s before broadcasting again.</i>",
                parse_mode="HTML",
            )
            return 0, 0
        _mark_broadcast(admin_uid)

    sent = failed = 0
    if users_collection is None:
        logger.error("Broadcast cancelled — DB down")
        return 0, 0

    total = await users_collection.count_documents({"is_banned": False})
    logger.info(f"Broadcast starting → {total} users")

    async for doc in users_collection.find({"is_banned": False}, {"user_id": 1}):
        result = await safe_send(context, doc["user_id"], message, parse_mode="HTML")
        if result:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

    analytics["broadcasts_sent"] += 1
    logger.info(f"Broadcast done — sent={sent} failed={failed}")
    return sent, failed


async def execute_media_broadcast(context, from_chat_id: int, message_id: int,
                                   admin_uid: int = None) -> tuple:
    """Copy a media message to all non-banned users."""
    if admin_uid is not None:
        wait = _check_rate_limit(admin_uid)
        if wait > 0:
            await safe_send(
                context, admin_uid,
                f"⏱ <i>Please wait {wait}s before broadcasting again.</i>",
                parse_mode="HTML",
            )
            return 0, 0
        _mark_broadcast(admin_uid)

    sent = failed = 0
    if users_collection is None:
        return 0, 0

    async for doc in users_collection.find({"is_banned": False}, {"user_id": 1}):
        try:
            await context.bot.copy_message(
                chat_id=doc["user_id"],
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

    analytics["broadcasts_sent"] += 1
    return sent, failed