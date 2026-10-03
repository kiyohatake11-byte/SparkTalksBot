import asyncio
import logging

from config import BROADCAST_SLEEP_SECONDS
from database import users_collection
from utils import box_card, safe_send

logger = logging.getLogger("sparktalks")


async def execute_broadcast(context, message: str) -> tuple:
    text = box_card(
        "Announcement",
        [
            {"type": "text", "content": message},
            {"type": "divider"},
            {"type": "text", "content": "— SparkTalks Team"},
        ],
        emoji="📢",
    )

    sent = failed = 0
    total = await users_collection.count_documents({})
    logger.info(f"Broadcast starting → {total} users")

    async for doc in users_collection.find({}, {"user_id": 1}):
        result = await safe_send(context, doc["user_id"], text, parse_mode="HTML")
        if result:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_SLEEP_SECONDS)

    logger.info(f"Broadcast done — sent={sent} failed={failed}")
    return sent, failed