import html
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest, RetryAfter
import asyncio

from config import OWNER_ID, OWNER_USERNAME

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# TIME HELPERS
# ══════════════════════════════════════════════════════════════

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def user_local_time(offset_hours: float = 0.0) -> datetime:
    return utcnow() + timedelta(hours=offset_hours)


def get_owner_link(prefill_text: str = None) -> str:
    if OWNER_USERNAME:
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", OWNER_USERNAME):
            logger.warning(f"Invalid OWNER_USERNAME: {OWNER_USERNAME!r}")
        else:
            base = f"https://t.me/{OWNER_USERNAME}"
            return f"{base}?text={quote(prefill_text)}" if prefill_text else base
    if OWNER_ID and OWNER_ID != 0:
        return f"tg://user?id={OWNER_ID}"
    return "https://t.me/your_telegram_username"


# ══════════════════════════════════════════════════════════════
# HTML HELPERS
# ══════════════════════════════════════════════════════════════

def to_bold(t: str) -> str:
    return f"<b>{t}</b>"


def to_italic(t: str) -> str:
    return f"<i>{t}</i>"


def to_underline(t: str) -> str:
    return f"<u>{t}</u>"


def to_code(t: str) -> str:
    return f"<code>{t}</code>"


_COMMAND_RE = re.compile(r"(/[a-zA-Z0-9_@]+)")


def safe_italic(text: str) -> str:
    """Wrap text in <i>, but leave commands (/cmd) untouched."""
    parts = _COMMAND_RE.split(text)
    rebuilt = []
    for part in parts:
        if part.startswith("/") and _COMMAND_RE.fullmatch(part):
            rebuilt.append(part)
        else:
            rebuilt.append(f"<b><i>{part}</i></b>")
    return "".join(rebuilt)


# ══════════════════════════════════════════════════════════════
# SIDEBAR STYLE — Primary design for all cards
# ══════════════════════════════════════════════════════════════

def box_card(title: str, blocks: list, emoji: str = "", width: int = None) -> str:
    """
    Sidebar-style card:
      ⚡  ✨  Title  ✨  ⚡
      ▎
      ▎ Content lines...
    Accepts blocks in the same schema as before:
      - {"type": "text", "content": "..."}
      - {"type": "line", "content": "..."}
      - {"type": "section", "emoji": "🎯", "heading": "..."}
      - {"type": "kv", "items": [("key", "val"), ...]}
      - {"type": "quote", "content": "..."}
      - {"type": "divider"}
    The `width` arg is kept for backward compat and ignored.
    """
    if emoji:
        header = f"{emoji}  ✨  <b>{title}</b>  ✨  {emoji}"
    else:
        header = f"✨  <b>{title}</b>  ✨"

    lines = [header, "▎"]

    for block in blocks:
        btype = block.get("type")

        if btype == "divider":
            lines.append("▎")

        elif btype == "text":
            content = block.get("content", "")
            for line in content.split("\n"):
                if line.strip():
                    lines.append(f"▎ {line}")
                else:
                    lines.append("▎")

        elif btype == "section":
            em = block.get("emoji", "")
            heading = block.get("heading", "")
            prefix = f"{em} " if em else ""
            lines.append(f"▎ {prefix}<b>{heading}</b>")

        elif btype == "line":
            lines.append(f"▎ {block.get('content', '')}")

        elif btype == "quote":
            content = block.get("content", "")
            lines.append(f'▎ 💡 <i>"{content}"</i>')

        elif btype == "kv":
            for key, val in block.get("items", []):
                lines.append(f"▎ {key} : {val}")

    return "\n".join(lines)


def box_simple(title: str, content: str, emoji: str = "") -> str:
    """Simple sidebar card: title + single text content."""
    return box_card(title, [{"type": "text", "content": content}], emoji=emoji)


def box_with_footer(title: str, blocks: list, footer_lines: list,
                    emoji: str = "") -> str:
    """Sidebar card + optional footer lines below."""
    box = box_card(title, blocks, emoji=emoji)
    if footer_lines:
        box += "\n\n" + "\n".join(footer_lines)
    return box


def spark_card(title: str, body: str, footer: str = None, emoji: str = "") -> str:
    box = box_simple(title, body, emoji=emoji)
    if footer:
        box += f"\n\n{footer}"
    return box


def card(title: str, body: str, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def sidebar(title: str, emoji: str, lines: list, tip: str = None) -> str:
    """
    Direct sidebar builder — no blocks needed.
    Usage:
        sidebar("Stats", "📊", ["Total: 100", "VIP: 12"], tip="Use /help")
    """
    parts = [f"{emoji}  ✨  <b>{title}</b>  ✨  {emoji}", "▎"]
    for l in lines:
        if l:
            parts.append(f"▎ {l}")
        else:
            parts.append("▎")
    if tip:
        parts.append("▎")
        parts.append(f"💡 <i>{tip}</i>")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# MESSAGE UTILS
# ══════════════════════════════════════════════════════════════

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


async def safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                    retries: int = 2, **kwargs):
    if not text or not text.strip():
        return None
    for attempt in range(retries):
        try:
            return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Forbidden:
            logger.info(f"User {chat_id} blocked the bot")
            return None
        except BadRequest as e:
            logger.warning(f"Bad request to {chat_id}: {e}")
            return None
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Send failed to {chat_id}: {e}")
                return None
            await asyncio.sleep(0.5)
    return None


async def safe_chat_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                           action: str = "typing"):
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=action)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# BUTTON HELPERS — safe style support
# ══════════════════════════════════════════════════════════════
# Telegram Bot API 9.0+ supports `style` on KeyboardButton:
#   "primary" (blue), "success" (green), "danger" (red)
# InlineKeyboardButton does NOT support style on Telegram's side,
# but we keep the helper future-proof — if PTB adds it, it works.

_KB_STYLE_SUPPORTED = None
_IKB_STYLE_SUPPORTED = None


def _check_kb_style() -> bool:
    """Cached check: does KeyboardButton support `style`?"""
    global _KB_STYLE_SUPPORTED
    if _KB_STYLE_SUPPORTED is None:
        from telegram import KeyboardButton
        try:
            KeyboardButton("x", style="primary")
            _KB_STYLE_SUPPORTED = True
        except Exception:
            _KB_STYLE_SUPPORTED = False
    return _KB_STYLE_SUPPORTED


def _check_ikb_style() -> bool:
    """Cached check: does InlineKeyboardButton support `style`?"""
    global _IKB_STYLE_SUPPORTED
    if _IKB_STYLE_SUPPORTED is None:
        from telegram import InlineKeyboardButton
        try:
            InlineKeyboardButton("x", callback_data="x", style="primary")
            _IKB_STYLE_SUPPORTED = True
        except Exception:
            _IKB_STYLE_SUPPORTED = False
    return _IKB_STYLE_SUPPORTED


def kb(text: str, style: str = None, **kwargs):
    """
    Create a KeyboardButton with optional style.
    style: "primary" | "success" | "danger" | None
    Silently drops style if PTB doesn't support it.
    """
    from telegram import KeyboardButton
    if style and _check_kb_style():
        kwargs["style"] = style
    return KeyboardButton(text, **kwargs)


def ikb(text: str, callback_data: str = None, url: str = None,
        style: str = None, **kwargs):
    """
    Create an InlineKeyboardButton with optional style.
    NOTE: Telegram API doesn't support style on inline buttons — this
    will silently be ignored. Kept for future compatibility.
    """
    from telegram import InlineKeyboardButton
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if style and _check_ikb_style():
        kwargs["style"] = style
    return InlineKeyboardButton(text, **kwargs)