"""Anti-link / anti-username / anti-flood / mute / bad-word filter.

⭐ FIXED:
  - Bug #11: handle_violation + handle_bad_word_violation now delete
    stale searching cards
  - Bug #23: VIP violation message no longer says "share when both agree"
    (contradicted the fact that we still block)
"""
import re
import logging
from collections import deque
from datetime import timedelta

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


_URL_RE = re.compile(r"(https?://|www\.|ftp://)[^\s]+", re.IGNORECASE)
_TG_RE = re.compile(r"(t\.me/|telegram\.me/|telegram\.dog/|tg://)[^\s]+", re.IGNORECASE)
_USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z][A-Za-z0-9_]{3,31}(?![A-Za-z0-9_])")
_OBFUSCATED_AT = re.compile(r"(\(|\[|\{)\s*(at|@|एट)\s*(\)|\]|\})", re.IGNORECASE)
_OBFUSCATED_DOT = re.compile(r"(\(|\[|\{)\s*(dot|\.|डॉट)\s*(\)|\]|\})", re.IGNORECASE)
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
    """Only normalize STRICT forms like (at) or [dot] — no bare words."""
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
    if norm != text:
        if re.search(r"\S+\s*@\s*\S+\.\S+", norm) or _PHONE_RE.search(norm):
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
    """Handle contact-sharing violation.

    ⭐ FIX Bug #23: VIP message no longer contradicts policy.
    ⭐ FIX Bug #11: searching card deleted on auto-mute.
    """
    if u.get("is_vip"):
        # ⭐ FIX Bug #23: removed misleading "share when both agree" line
        await safe_send(
            context, uid,
            "🛡️  ✨  <b>Contact Sharing Blocked</b>  ✨  🛡️\n"
            "▎\n"
            "▎ ⚠️ Your message was blocked (safety policy).\n"
            "▎\n"
            f"▎ 🚫 Detected : {reason.replace('_', ' ').title()}\n"
            "▎\n"
            "▎ 💡 <i>Contact info is not allowed in chat.</i>",
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

        # ⭐ FIX Bug #11: delete searching card
        try:
            from services.matching import _delete_searching_card
            await _delete_searching_card(context, uid)
        except Exception:
            pass

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


# ══════════════════════════════════════════════════════════════
# BAD WORD AUTO-MODERATION
# ══════════════════════════════════════════════════════════════

def contains_bad_word(text: str):
    """Check text against bad words regex.
    Returns: (True, matched_word, rule_dict) or (False, None, None)
    """
    if not text:
        return False, None, None
    import state as _st
    if not _st.bad_words or _st.bad_words_re is None:
        return False, None, None
    try:
        m = _st.bad_words_re.search(text)
    except Exception:
        return False, None, None
    if not m:
        return False, None, None
    matched = m.group(1).lower()
    for rule in _st.bad_words:
        if rule["word"].lower() == matched:
            return True, matched, rule
    return True, matched, {"word": matched, "action": "ban", "duration": 0}


async def handle_bad_word_violation(context, uid: int, u: dict,
                                     matched: str, rule: dict) -> None:
    """Auto-ban or auto-mute a user for using a prohibited word.

    ⭐ FIX Bug #11: delete stale searching card.
    ⭐ FIX Bug #19: save_user_to_db after DB writes.
    """
    from state import (
        queue, queue_set, queue_lock, muted_users,
        analytics, admin_logs,
    )
    from database import users_collection, save_user_to_db
    from utils import safe_send, utcnow, esc

    action = rule.get("action", "ban")
    word = rule.get("word", matched)
    duration = int(rule.get("duration", 0) or 0)

    # Disconnect from chat
    if u.get("partner"):
        try:
            from services.matching import disconnect
            await disconnect(context, uid, u["partner"], ender_id=uid)
        except Exception as e:
            logger.debug(f"bad_word disconnect failed: {e}")

    # ⭐ FIX Bug #11: delete searching card
    try:
        from services.matching import _delete_searching_card
        await _delete_searching_card(context, uid)
    except Exception:
        pass

    # Remove from queue
    try:
        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
    except Exception:
        pass
    u["state"] = "IDLE"

    # Log
    try:
        admin_logs.append({
            "time": utcnow().strftime("%H:%M"),
            "admin": 0,
            "action": f"AUTO-{action.upper()}: '{word}' → {uid}",
        })
    except Exception:
        pass

    # ═══ BAN ═══
    if action == "ban":
        u["is_banned"] = True
        u["banned_reason"] = f"Auto-ban: prohibited word '{word}'"
        u["banned_at"] = utcnow()
        u["banned_by"] = 0

        if users_collection is not None:
            try:
                await users_collection.update_one(
                    {"user_id": uid},
                    {"$set": {
                        "is_banned": True,
                        "banned_reason": f"Auto-ban: prohibited word '{word}'",
                        "banned_at": utcnow(),
                        "banned_by": 0,
                    }},
                )
            except Exception as e:
                logger.error(f"bad_word ban DB write failed: {e}")

        # ⭐ FIX Bug #19: re-queue fresh snapshot
        try:
            await save_user_to_db(uid, u)
        except Exception:
            pass

        analytics["violations_today"] += 1

        await safe_send(
            context, uid,
            "🚫  ✨  <b>Access Denied</b>  ✨  🚫\n"
            "▎\n"
            "▎ You have been <b>permanently banned</b>.\n"
            "▎\n"
            "▎ 📝 Reason : Prohibited content detected\n"
            "▎\n"
            "💡 <i>Use /appeal to request review.</i>",
            parse_mode="HTML",
        )
        logger.warning(f"🚫 AUTO-BAN: {uid} used prohibited word '{word}'")

    # ═══ MUTE ═══
    elif action == "mute":
        if duration <= 0:
            duration = 300

        unmute_at = utcnow() + timedelta(seconds=duration)
        muted_users[uid] = utcnow().timestamp() + duration
        u["muted_until"] = unmute_at

        if users_collection is not None:
            try:
                await users_collection.update_one(
                    {"user_id": uid},
                    {"$set": {"muted_until": unmute_at}},
                )
            except Exception as e:
                logger.error(f"bad_word mute DB write failed: {e}")

        # ⭐ FIX Bug #19: re-queue fresh snapshot
        try:
            await save_user_to_db(uid, u)
        except Exception:
            pass

        analytics["violations_today"] += 1
        analytics["mutes_today"] += 1

        minutes = duration // 60
        time_str = f"{minutes} min" if minutes >= 1 else f"{duration}s"

        await safe_send(
            context, uid,
            "🔇  ✨  <b>Muted</b>  ✨  🔇\n"
            "▎\n"
            "▎ Prohibited content detected.\n"
            "▎\n"
            f"▎ ⏱ Muted for <b>{time_str}</b>\n"
            "▎\n"
            "💡 <i>Repeated violations → permanent ban.</i>",
            parse_mode="HTML",
        )
        logger.warning(
            f"🔇 AUTO-MUTE: {uid} used prohibited word '{word}' for {time_str}"
        )