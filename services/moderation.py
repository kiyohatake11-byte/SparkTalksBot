"""Anti-link / anti-username / anti-flood / mute."""
import re
import logging
from collections import deque

from telegram.ext import ContextTypes

from config import (
    ANTI_LINK_ENABLED, ANTI_LINK_WARN_LIMIT,
    FLOOD_MSG_LIMIT, FLOOD_WINDOW_SECONDS, FLOOD_MUTE_SECONDS,
)
from state import (
    muted_users, flood_buckets, link_warn_count,
    users, queue, queue_set, queue_lock, analytics,
)
from utils import safe_send, utcnow

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# PATTERNS
# ══════════════════════════════════════════════════════════════

_URL_RE = re.compile(r"(https?://|www\.|ftp://)[^\s]+", re.IGNORECASE)
_TG_RE = re.compile(r"(t\.me/|telegram\.me/|telegram\.dog/|tg://)[^\s]+", re.IGNORECASE)
_USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z][A-Za-z0-9_]{3,31}(?![A-Za-z0-9_])")
_OBFUSCATED_AT = re.compile(r"(\(|\[|\{|\s)?\s*(at|@|एट)\s*(\)|\]|\}|\s)?", re.IGNORECASE)
_OBFUSCATED_DOT = re.compile(r"(\(|\[|\{|\s)?\s*(dot|\.|डॉट)\s*(\)|\]|\}|\s)?", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<![\d])(\+?\d[\d\s\-\(\)]{8,15}\d)(?![\d])")

_SOCIAL_KEYWORDS = re.compile(
    r"\b(instagram|insta|snapchat|snap|whatsapp|wa\.me|discord|kik|"
    r"facebook|fb|telegram|signal|vk|twitter|tiktok|onlyfans|"
    r"kik me|dm me|message me on|text me on|add me on|"
    r"my insta|my snap|my number|mera number|meri id|"
    r"मेरा नंबर|मेरी आईडी|मेरा इंस्टा)\b",
    re.IGNORECASE,
)


def _normalize_obfuscated(text: str) -> str:
    text = _OBFUSCATED_AT.sub(" @ ", text)
    text = _OBFUSCATED_DOT.sub(" . ", text)
    return text


def contains_contact_info(text: str) -> tuple[bool, str]:
    if not text:
        return False, ""
    if _URL_RE.search(text):
        return True, "link"
    if _TG_RE.search(text):
        return True, "telegram_link"
    if _USERNAME_RE.search(text):
        return True, "username"
    if _SOCIAL_KEYWORDS.search(text):
        norm = _normalize_obfuscated(text)
        if _URL_RE.search(norm) or _USERNAME_RE.search(norm) or _PHONE_RE.search(norm):
            return True, "social_promo"
        if re.search(r"\b(dm|contact|add|message|text|follow)\b", text, re.IGNORECASE):
            if "@" in text or re.search(r"\b\d{6,}\b", text):
                return True, "social_promo"
    norm = _normalize_obfuscated(text)
    if norm != text and re.search(r"\S+\s*@\s*\S+\.\S+", norm):
        return True, "obfuscated_contact"
    m = _PHONE_RE.search(text)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 10:
            return True, "phone"
    return False, ""


def is_flooding(uid: int) -> bool:
    now = utcnow().timestamp()
    bucket = flood_buckets.get(uid)
    if bucket is None:
        bucket = deque(maxlen=FLOOD_MSG_LIMIT + 5)
        flood_buckets[uid] = bucket
    bucket.append(now)
    cutoff = now - FLOOD_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return len(bucket) > FLOOD_MSG_LIMIT


def is_muted(uid: int) -> bool:
    unmute_at = muted_users.get(uid)
    if unmute_at is None:
        return False
    if utcnow().timestamp() >= unmute_at:
        muted_users.pop(uid, None)
        return False
    return True


def mute_remaining_seconds(uid: int) -> int:
    unmute_at = muted_users.get(uid)
    if unmute_at is None:
        return 0
    return max(0, int(unmute_at - utcnow().timestamp()))


async def handle_violation(context: ContextTypes.DEFAULT_TYPE, uid: int, u: dict,
                           reason: str, msg_text: str):
    if u.get("is_vip"):
        await safe_send(
            context, uid,
            "🛡️  ✨  <b>Contact Sharing Blocked</b>  ✨  🛡️\n"
            "▎\n"
            "▎ ⚠️ Your message was blocked (safety policy).\n"
            "▎\n"
            f"▎ 🚫 Detected : {reason.replace('_', ' ').title()}\n"
            "▎\n"
            "▎ 💡 <i>Please share contacts only when both agree.</i>",
            parse_mode="HTML",
        )
        return

    count = link_warn_count.get(uid, 0) + 1
    link_warn_count[uid] = count

    if count >= ANTI_LINK_WARN_LIMIT:
        muted_users[uid] = utcnow().timestamp() + FLOOD_MUTE_SECONDS
        link_warn_count[uid] = 0
        analytics["mutes_today"] += 1

        if u.get("partner"):
            try:
                from services.matching import disconnect
                await disconnect(context, uid, u["partner"], ender_id=uid)
            except Exception:
                pass

        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
        if u.get("state") == "SEARCHING":
            u["state"] = "IDLE"

        minutes = FLOOD_MUTE_SECONDS // 60
        await safe_send(
            context, uid,
            "🔇  ✨  <b>Auto-Muted</b>  ✨  🔇\n"
            "▎\n"
            "▎ ⚠️ Too many contact-sharing violations.\n"
            "▎\n"
            f"▎ ⏱ Muted for <b>{minutes} min</b>\n"
            "▎\n"
            "▎ 💡 <i>Repeated violations → permanent ban.</i>",
            parse_mode="HTML",
        )
        logger.warning(f"Auto-muted {uid}")
        return

    remaining = ANTI_LINK_WARN_LIMIT - count
    await safe_send(
        context, uid,
        "⚠️  ✨  <b>Contact Sharing Not Allowed</b>  ✨  ⚠️\n"
        "▎\n"
        "▎ 🚫 Your message was blocked.\n"
        "▎\n"
        f"▎ Detected : {reason.replace('_', ' ').title()}\n"
        f"▎ Warning {count}/{ANTI_LINK_WARN_LIMIT}\n"
        f"▎ {remaining} more → auto-mute {FLOOD_MUTE_SECONDS // 60} min\n"
        "▎\n"
        "▎ 🛡️ <i>SparkTalks blocks links, usernames, phone numbers, social handles.</i>",
        parse_mode="HTML",
    )


def is_contact_share(msg) -> bool:
    return bool(getattr(msg, "contact", None))