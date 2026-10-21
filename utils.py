import html
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest, RetryAfter
import asyncio

from config import OWNER_ID, OWNER_USERNAME, LOG_CHANNEL_ID

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


def esc(s) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=False)


_COMMAND_RE = re.compile(r"(/[a-zA-Z0-9_@]+)")


def safe_italic(text: str) -> str:
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
    return box_card(title, [{"type": "text", "content": content}], emoji=emoji)


def box_with_footer(title: str, blocks: list, footer_lines: list,
                    emoji: str = "") -> str:
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

    parse_mode = kwargs.get("parse_mode")

    for attempt in range(retries):
        try:
            return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)

        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)

        except Forbidden:
            logger.info(f"User {chat_id} blocked the bot")
            return None

        except BadRequest as e:
            err_str = str(e).lower()

            if parse_mode and ("parse entities" in err_str or "can't parse" in err_str):
                logger.warning(
                    f"HTML parse failed for {chat_id} — retrying as plain text. "
                    f"Original: {e}"
                )
                kwargs.pop("parse_mode", None)
                parse_mode = None
                try:
                    return await context.bot.send_message(
                        chat_id=chat_id, text=text, **kwargs
                    )
                except Exception as e2:
                    logger.error(f"Plain-text retry also failed for {chat_id}: {e2}")
                    return None

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
# BUTTON HELPERS
# ══════════════════════════════════════════════════════════════

_KB_STYLE_SUPPORTED = None
_IKB_STYLE_SUPPORTED = None


def _check_kb_style() -> bool:
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
    from telegram import KeyboardButton
    if style and _check_kb_style():
        kwargs["style"] = style
    return KeyboardButton(text, **kwargs)


def ikb(text: str, callback_data: str = None, url: str = None,
        style: str = None, **kwargs):
    from telegram import InlineKeyboardButton
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if style and _check_ikb_style():
        kwargs["style"] = style
    return InlineKeyboardButton(text, **kwargs)


# ══════════════════════════════════════════════════════════════
# ⭐ LOG CHANNEL — send events to staff channel or OWNER_ID DM
# ══════════════════════════════════════════════════════════════

_LEVEL_EMOJI = {
    "info": "🟢",
    "warn": "🟠",
    "critical": "🔴",
    "admin": "🛡️",
    "moderation": "🚫",
    "user": "👤",
    "payment": "💰",
    "system": "⚙️",
}

_EVENT_EMOJI = {
    # System
    "BOT_START": "🟢",
    "BOT_STOP": "🛑",
    "DEPLOY_DETECTED": "🚀",
    "WEBHOOK_FAIL": "❌",
    "DB_DISCONNECT": "🔴",
    "PRELOAD_STATS": "📥",
    # Admin
    "BAN": "🔴",
    "UNBAN": "✅",
    "KICK": "👢",
    "MUTE": "🔇",
    "UNMUTE": "🔊",
    "UNMUTEALL": "🔊",
    "WARN": "⚠️",
    "VIP_GRANT": "👑",
    "VIP_REMOVE": "⌛",
    "VIP_BACKFILL": "📝",
    "VERIFY": "✅",
    "UNVERIFY": "❌",
    "RESETPROFILE": "🔄",
    "RESETSTATS": "📊",
    "PURGE": "🧨",
    "FORCEDELETE": "🗑️",
    "ADMIN_PROMOTE": "🛡️",
    "ADMIN_DEMOTE": "🛡️",
    "MAINTENANCE_ON": "🚧",
    "MAINTENANCE_OFF": "✅",
    "CLEARCACHE": "🧹",
    "BANWORD": "🚫",
    "MUTEWORD": "🔇",
    "REMOVEWORD": "❌",
    # Auto-mod
    "AUTO_BAN": "🔴",
    "AUTO_MUTE": "🔇",
    "COMMUNITY_LEAVE": "🚪",
    # User
    "REPORT": "🚨",
    "BLOCK": "🚫",
    "REFERRAL_MILESTONE": "🎁",
    # Payment
    "PAYMENT_SUCCESS": "💰",
    "PAYMENT_FAIL": "❌",
    "VIP_EXPIRE": "⌛",
    # Broadcast
    "BROADCAST_START": "📢",
    "BROADCAST_END": "📢",
    # Error
    "ERROR_CRITICAL": "🚨",
}


def _resolve_log_channel():
    """Return channel chat_id (int or @username), or OWNER_ID fallback."""
    raw = (LOG_CHANNEL_ID or "").strip()
    if raw:
        if raw.startswith("@"):
            return raw
        try:
            return int(raw)
        except ValueError:
            pass
    return OWNER_ID if OWNER_ID else None


async def _lookup_user_name(uid: int) -> str:
    """Resolve user name from memory, then DB. Used by log_event."""
    try:
        from state import users
        u = users.get(uid)
        if u:
            return esc(u.get("name") or "—")
        from database import users_collection
        if users_collection is not None:
            d = await users_collection.find_one({"user_id": uid}, {"name": 1})
            if d:
                return esc(d.get("name") or "—")
    except Exception:
        pass
    return "—"


async def log_event(context, event: str, *,
                    category: str = "system",
                    level: str = "info",
                    actor_id: int = None,
                    target_id: int = None,
                    details: dict = None,
                    silent: bool = False) -> bool:
    """Send event to staff log channel (or OWNER_ID DM if channel unset).

    Args:
        context: PTB context (bot)
        event: event name (UPPER_SNAKE) — e.g. "BAN", "REPORT"
        category: system/admin/moderation/user/payment
        level: info/warn/critical
        actor_id: who triggered (admin_id or user_id)
        target_id: whom it affected
        details: dict of extra lines {"Reason": "spam", "Duration": "5 min"}
        silent: send without notification sound

    Returns: True if sent successfully.
    """
    if context is None or not context.bot:
        return False

    chat_id = _resolve_log_channel()
    if not chat_id:
        logger.debug(f"log_event: no channel and no OWNER_ID — event '{event}' dropped")
        return False

    emoji = _EVENT_EMOJI.get(event) or _LEVEL_EMOJI.get(level, "ℹ️")
    header = f"{emoji}  <b>{esc(event)}</b>"

    lines = [header, "▎"]

    if actor_id:
        name = await _lookup_user_name(actor_id)
        lines.append(f"▎ 👤 Actor: {name} <code>{actor_id}</code>")
    if target_id:
        name = await _lookup_user_name(target_id)
        lines.append(f"▎ 🎯 Target: {name} <code>{target_id}</code>")

    if details:
        for k, v in details.items():
            lines.append(f"▎ {esc(str(k))}: {esc(str(v))}")

    lines.append(f"▎ 🕐 {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    body = "\n".join(lines)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=body,
            parse_mode="HTML",
            disable_notification=silent,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logger.warning(f"log_event send failed to {chat_id}: {e}")
        if chat_id != OWNER_ID and OWNER_ID:
            try:
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=body + f"\n\n⚠️ <i>Channel {chat_id} failed: {esc(str(e)[:80])}</i>",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return True
            except Exception as e2:
                logger.error(f"log_event fallback DM failed: {e2}")
        return False