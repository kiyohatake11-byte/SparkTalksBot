"""Community channel — force join after N matches."""
import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError

from config import (
    COMMUNITY_CHANNEL_USERNAME, COMMUNITY_CHANNEL_LINK,
    COMMUNITY_CHANNEL_ID, FORCE_JOIN_AFTER_MATCHES,
)
from state import users
from database import save_user_to_db
from utils import safe_send

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _get_channel_chat_id():
    """Return chat_id for get_chat_member, or None."""
    if COMMUNITY_CHANNEL_ID:
        try:
            return int(COMMUNITY_CHANNEL_ID)
        except ValueError:
            logger.warning(f"Invalid COMMUNITY_CHANNEL_ID: {COMMUNITY_CHANNEL_ID!r}")
    if COMMUNITY_CHANNEL_USERNAME:
        return f"@{COMMUNITY_CHANNEL_USERNAME}"
    return None


def _get_invite_link():
    if COMMUNITY_CHANNEL_LINK:
        return COMMUNITY_CHANNEL_LINK
    if COMMUNITY_CHANNEL_USERNAME:
        return f"https://t.me/{COMMUNITY_CHANNEL_USERNAME}"
    return None


def is_configured() -> bool:
    return bool(_get_channel_chat_id() and _get_invite_link())


async def check_membership(context, uid: int) -> bool:
    """
    Return True if user is a member of the community channel.
    If channel not configured → True (feature disabled).
    If bot lacks permission to check → True (lenient fallback).
    """
    chat_id = _get_channel_chat_id()
    if not chat_id:
        return True

    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=uid)
    except TelegramError as e:
        logger.warning(f"get_chat_member failed for {uid} in {chat_id}: {e}")
        # Bot probably not admin — don't block users
        return True

    status = getattr(member, "status", "")
    # PTB may return enum or string
    if hasattr(status, "value"):
        status = status.value
    status = str(status).lower()

    return status in ("member", "administrator", "creator")


async def should_prompt_join(u: dict) -> bool:
    """Return True if user must join before matching."""
    if not is_configured():
        return False
    if u.get("joined_community"):
        return False
    if u.get("total_matches", 0) < FORCE_JOIN_AFTER_MATCHES:
        return False
    return True


def _build_join_card() -> str:
    return (
        "📢  ✨  <b>Join Our Community</b>  ✨  📢\n"
        "▎\n"
        "▎ 🎉 You've completed your first matches!\n"
        "▎\n"
        "▎ ⚠️ To continue matching, please join\n"
        "▎ our official community channel.\n"
        "▎\n"
        "▎ 📋  <b>Steps</b>\n"
        "▎   ├ 1️⃣ Tap <b>Join Community</b>\n"
        "▎   ├ 2️⃣ Join the channel\n"
        "▎   └ 3️⃣ Come back & tap <b>Verify</b>\n"
        "▎\n"
        "💡 <i>This keeps our community safe & spam-free.</i>"
    )


def _build_join_keyboard() -> InlineKeyboardMarkup:
    link = _get_invite_link()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Community", url=link)],
        [InlineKeyboardButton("✅ I've Joined — Verify",
                              callback_data="CHECK_COMMUNITY_JOIN")],
    ])


async def prompt_join(context, uid: int) -> bool:
    """Send join prompt with Verify button."""
    sent = await safe_send(
        context, uid,
        _build_join_card(),
        reply_markup=_build_join_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return bool(sent)


async def verify_and_unlock(context, uid: int) -> bool:
    """
    Verify membership and mark joined_community=True.
    Returns True on success.
    """
    u = users.get(uid)
    if not u:
        return False

    ok = await check_membership(context, uid)
    if not ok:
        return False

    u["joined_community"] = True
    await save_user_to_db(uid, u)
    logger.info(f"✅ User {uid} verified community join")
    return True