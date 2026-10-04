import asyncio
import html
import logging

from config import BROADCAST_SLEEP_SECONDS
from database import users_collection
from utils import box_card, safe_send

logger = logging.getLogger("sparktalks")


async def execute_broadcast(context, message: str) -> tuple:
    safe_message = html.escape(message)
    text = box_card(
        "Announcement",
        [
            {"type": "text", "content": safe_message},
            {"type": "divider"},
            {"type": "text", "content": "- SparkTalks Team"},
        ],
        emoji="\U0001F4E2",
    )

    sent = failed = 0
    if users_collection is None:
        logger.error("Broadcast cancelled - MongoDB not connected")
        return 0, 0

    total = await users_collection.count_documents({"is_banned": False})
    logger.info(f"Broadcast starting -> {total} users (banned excluded)")

    async for doc in users_collection.find({"is_banned": False}, {"user_id": 1}):
        result = await safe_send(context, doc["user_id"], text, parse_mode="HTML")
        if result:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

    logger.info(f"Broadcast done - sent={sent} failed={failed}")
    return sent, failed
