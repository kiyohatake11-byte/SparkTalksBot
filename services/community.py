"""Community channel — force join after N matches."""
import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError, BadRequest, Forbidden

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
    Return True ONLY if we can CONFIRM user is a member.

    ⚠️ FAIL-CLOSED behavior:
      - If channel not configured → True (feature disabled, don't block)
      - If bot lacks permission / API error → False (block, don't trust)
      - If user is member/admin/creator → True
      - Otherwise → False
    """
    chat_id = _get_channel_chat_id()
    if not chat_id:
        # Feature not configured — don't block anyone
        return True

    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=uid)
    except BadRequest as e:
        err = str(e).lower()
        # User not found / never interacted with channel
        if "user not found" in err or "participant" in err:
            logger.info(f"Community check: {uid} not found in {chat_id} → NOT member")
            return False
        # Chat not found / bot not admin / no rights
        if ("chat not found" in err or "not enough rights" in err
                or "member list is inaccessible" in err
                or "bot is not a member" in err):
            logger.error(
                f"❌ COMMUNITY CHECK FAILED: Bot is not admin in {chat_id} "
                f"or lacks access. Error: {e}"
            )
            # FAIL-CLOSED — don't verify, alert admin
            return False
        logger.warning(f"Community check BadRequest for {uid}: {e}")
        return False
    except Forbidden as e:
        logger.error(f"❌ Community check Forbidden for {uid}: {e}")
        return False
    except TelegramError as e:
        logger.error(f"❌ Community check TelegramError for {uid}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Community check unexpected error for {uid}: {e}",
                     exc_info=True)
        return False

    # ─── Extract status (works with both enum & string) ───
    status = getattr(member, "status", "")
    if hasattr(status, "value"):
        status = status.value
    status = str(status).lower()

    # ─── Determine membership ────────────────────────────
    if status in ("creator", "administrator", "member"):
        return True

    if status == "restricted":
        # Restricted users can still be "in" the channel
        is_member = getattr(member, "is_member", False)
        return bool(is_member)

    # "left", "kicked", "banned", or anything unknown → NOT a member
    logger.info(f"Community check: {uid} status='{status}' → NOT member")
    return False


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
    )
    return bool(sent)


async def verify_and_unlock(context, uid: int) -> bool:
    """
    Verify membership and mark joined_community=True.
    Returns True ONLY if membership is CONFIRMED.

    ⚠️ FAIL-CLOSED: returns False on any error.
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