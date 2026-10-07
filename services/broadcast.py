import asyncio
import logging

from config import BROADCAST_SLEEP_SECONDS
from database import users_collection
from utils import safe_send
from state import analytics

logger = logging.getLogger("sparktalks")


async def execute_broadcast(context, message: str) -> tuple:
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


async def execute_media_broadcast(context, from_chat_id: int, message_id: int) -> tuple:
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