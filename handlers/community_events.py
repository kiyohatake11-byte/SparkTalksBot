"""Instant community membership events via chat_member updates."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import COMMUNITY_CHANNEL_ID, COMMUNITY_CHANNEL_USERNAME
from state import users, community_check_cache
from database import save_user_to_db

logger = logging.getLogger("sparktalks")


def _resolve_channel_chat_id():
    if COMMUNITY_CHANNEL_ID:
        try:
            return int(COMMUNITY_CHANNEL_ID)
        except ValueError:
            pass
    if COMMUNITY_CHANNEL_USERNAME:
        return f"@{COMMUNITY_CHANNEL_USERNAME}"
    return None


def _is_community_chat(update_chat) -> bool:
    """Check if this chat_member update is for our community channel."""
    target = _resolve_channel_chat_id()
    if not target:
        return False
    try:
        if isinstance(target, int):
            return update_chat.id == target
        uname = target.lstrip("@").lower()
        chat_uname = (update_chat.username or "").lower()
        return chat_uname == uname
    except Exception:
        return False


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Instantly handle community channel membership changes.

    - User JOINS  → auto-verify (flag = True)
    - User LEAVES → reset flag, clear cache

    Requires bot to be admin in the channel.
    """
    cm = update.chat_member
    if not cm:
        return

    if not _is_community_chat(cm.chat):
        return

    uid = cm.new_chat_member.user.id
    if cm.new_chat_member.user.is_bot:
        return

    new_status = cm.new_chat_member.status
    if hasattr(new_status, "value"):
        new_status = new_status.value
    new_status = str(new_status).lower()

    old_status = cm.old_chat_member.status
    if hasattr(old_status, "value"):
        old_status = old_status.value
    old_status = str(old_status).lower()

    # ⭐ Always invalidate cache — next /next will be fresh
    community_check_cache.pop(uid, None)

    u = users.get(uid)
    if not u:
        logger.debug(
            f"Community event for {uid} ({old_status}→{new_status}) — "
            f"user not cached"
        )
        return

    is_member_now = new_status in ("creator", "administrator", "member")
    if new_status == "restricted":
        is_member_now = bool(getattr(cm.new_chat_member, "is_member", False))

    if is_member_now:
        if not u.get("joined_community"):
            u["joined_community"] = True
            await save_user_to_db(uid, u)
            logger.info(f"⚡ INSTANT: {uid} JOINED community → auto-verified")
    else:
        if u.get("joined_community"):
            u["joined_community"] = False
            await save_user_to_db(uid, u)
            logger.info(f"⚡ INSTANT: {uid} LEFT community ({new_status}) → flag reset")