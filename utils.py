import html
import logging
from datetime import datetime, timezone
from urllib.parse import quote
from telegram.ext import ContextTypes

from config import OWNER_ID, OWNER_USERNAME

logger = logging.getLogger("sparktalks")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_owner_link(prefill_text: str = None) -> str:
    if OWNER_USERNAME:
        base = f"https://t.me/{OWNER_USERNAME}"
        return f"{base}?text={quote(prefill_text)}" if prefill_text else base
    if OWNER_ID and OWNER_ID != 0:
        return f"tg://user?id={OWNER_ID}"
    return "https://t.me/your_telegram_username"


def spark_card(title: str, body: str, footer: str = None) -> str:
    card = "✨ 𝗦𝗽𝗮𝗿𝗸𝗧𝗮𝗹𝗸𝘀 ✨\n"
    card += "───────────────────────────────\n\n"
    card += f"{body}\n"
    if footer:
        card += "\n───────────────────────────────\n"
        card += f"<i>❖ {footer}</i>"
    return card


def split_message(text: str, max_len: int = 4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    return parts


async def safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs):
    try:
        return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        logger.error(f"Send failed to {chat_id}: {e}")
        return None
