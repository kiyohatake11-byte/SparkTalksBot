"""Achievement badges — unlock & notify."""
import logging
from telegram.ext import ContextTypes

from state import users
from database import save_user_to_db
from utils import safe_send, utcnow

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# ACHIEVEMENTS CATALOG
# ══════════════════════════════════════════════════════════════
ACHIEVEMENTS = {
    "first_match": {
        "name": "First Match",
        "emoji": "🎯",
        "desc": "Complete your first match",
    },
    "ten_matches": {
        "name": "Explorer",
        "emoji": "🧭",
        "desc": "Complete 10 matches",
    },
    "fifty_matches": {
        "name": "Social Butterfly",
        "emoji": "🦋",
        "desc": "Complete 50 matches",
    },
    "hundred_chats": {
        "name": "Chatterbox",
        "emoji": "💬",
        "desc": "Have 100 conversations",
    },
    "first_vip": {
        "name": "VIP Member",
        "emoji": "👑",
        "desc": "Purchase your first VIP plan",
    },
    "referrer": {
        "name": "Recruiter",
        "emoji": "🎁",
        "desc": "Refer 1 friend to SparkTalks",
    },
    "ten_referrals": {
        "name": "Ambassador",
        "emoji": "🌟",
        "desc": "Refer 10 friends",
    },
    "voice_master": {
        "name": "Voice Master",
        "emoji": "🎙️",
        "desc": "Start a voice room",
    },
    "theme_collector": {
        "name": "Stylist",
        "emoji": "🎨",
        "desc": "Try 3 different chat themes",
    },
    "verified": {
        "name": "Verified",
        "emoji": "✅",
        "desc": "Get verified by admin",
    },
}


async def unlock_achievement(context: ContextTypes.DEFAULT_TYPE,
                              uid: int, key: str) -> bool:
    """Unlock an achievement for a user. Returns True if newly unlocked."""
    if key not in ACHIEVEMENTS:
        logger.warning(f"Unknown achievement: {key}")
        return False

    u = users.get(uid)
    if not u:
        return False

    unlocked = u.setdefault("achievements", [])
    if key in unlocked:
        return False

    unlocked.append(key)
    await save_user_to_db(uid, u)

    meta = ACHIEVEMENTS[key]
    try:
        await safe_send(
            context, uid,
            f"🏆  ✨  <b>Achievement Unlocked!</b>  ✨  🏆\n"
            f"▎\n"
            f"▎ {meta['emoji']} <b>{meta['name']}</b>\n"
            f"▎\n"
            f"▎ <i>{meta['desc']}</i>\n"
            f"▎\n"
            f"▎ 💡 View all: /achievements",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug(f"Achievement notify failed for {uid}: {e}")

    logger.info(f"🏆 {uid} unlocked '{key}'")
    return True


def get_unlocked(u: dict) -> list:
    """Return list of unlocked achievement keys."""
    return [k for k in (u.get("achievements") or []) if k in ACHIEVEMENTS]