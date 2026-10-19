"""VIP Reconnect — reconnect with a specific partner by ID."""
import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    RECONNECT_DAILY_LIMIT, RECONNECT_BUSY_MSG, BOT_USERNAME,
)
from state import users, queue_set, queue_lock, reconnect_daily, analytics
from database import get_user, save_user_to_db
from utils import box_card, box_simple, safe_send, utcnow

logger = logging.getLogger("sparktalks")


def _today_str() -> str:
    return utcnow().strftime("%Y-%m-%d")


def _get_reconnect_count(uid: int) -> int:
    entry = reconnect_daily.get(uid)
    if not entry or entry.get("date") != _today_str():
        reconnect_daily[uid] = {"date": _today_str(), "count": 0}
        return 0
    return entry["count"]


def _increment_reconnect(uid: int):
    entry = reconnect_daily.get(uid)
    if not entry or entry.get("date") != _today_str():
        reconnect_daily[uid] = {"date": _today_str(), "count": 1}
    else:
        entry["count"] += 1


async def reconnect_with_partner(context: ContextTypes.DEFAULT_TYPE, uid: int,
                                  partner_id: int = None) -> bool:
    """Reconnect to partner_id (or _last_partner_id if None)."""
    u = await get_user(uid)
    if not u:
        return False

    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
        ]])
        return await safe_send(
            context, uid,
            box_card("VIP Only", [
                {"type": "text", "content": "⚠️ Reconnect is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content": "Upgrade to unlock reconnect + save partner!"},
            ], emoji="👑"),
            reply_markup=kb, parse_mode="HTML",
        )

    if u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Busy", "⚠️ You're already in a chat. Use /end first.", emoji="⚠️"),
            parse_mode="HTML",
        )

    # ⭐ Prefer explicit partner_id over _last_partner_id
    target_id = partner_id or u.get("_last_partner_id")
    if not target_id:
        return await safe_send(
            context, uid,
            box_simple("No Recent Partner",
                       "⚠️ You haven't chatted with anyone recently.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    try:
        target_id = int(target_id)
    except (ValueError, TypeError):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ Invalid partner.", emoji="⚠️"),
            parse_mode="HTML",
        )

    used = _get_reconnect_count(uid)
    if used >= RECONNECT_DAILY_LIMIT:
        return await safe_send(
            context, uid,
            box_card("Limit Reached", [
                {"type": "text", "content":
                    f"🕐 You've used all {RECONNECT_DAILY_LIMIT} reconnects today."},
                {"type": "divider"},
                {"type": "text", "content": "Resets at midnight UTC."},
            ], emoji="⏰"),
            parse_mode="HTML",
        )

    partner = users.get(target_id)
    if not partner:
        partner = await get_user(target_id)
    if not partner:
        return await safe_send(
            context, uid,
            box_simple("Partner Unavailable",
                       "😕 Partner is offline. Try later.",
                       emoji="😕"),
            parse_mode="HTML",
        )

    if partner.get("is_banned"):
        return await safe_send(
            context, uid,
            box_simple("Partner Unavailable",
                       "🚫 This user is no longer available.",
                       emoji="🚫"),
            parse_mode="HTML",
        )

    if partner.get("partner") or partner.get("state") == "SEARCHING":
        return await safe_send(context, uid, RECONNECT_BUSY_MSG, parse_mode="HTML")

    if target_id in (u.get("blocked_users") or []):
        return await safe_send(
            context, uid,
            box_simple("Blocked", "🚫 You blocked this user.", emoji="🚫"),
            parse_mode="HTML",
        )
    if uid in (partner.get("blocked_users") or []):
        return await safe_send(
            context, uid,
            box_simple("Unavailable",
                       "🚫 This user has blocked you.",
                       emoji="🚫"),
            parse_mode="HTML",
        )

    async with queue_lock:
        from state import queue
        for pid in (uid, target_id):
            try:
                queue.remove(pid)
            except Exception:
                pass
            queue_set.discard(pid)

    from services.matching import connect_users
    try:
        await connect_users(context, uid, target_id)
        _increment_reconnect(uid)
        analytics["reconnects_today"] += 1
        logger.info(f"Reconnect: {uid} ↔ {target_id}")
        return True
    except Exception as e:
        logger.error(f"Reconnect failed: {e}", exc_info=True)
        return await safe_send(
            context, uid,
            box_simple("Failed", "⚠️ Reconnect failed. Try again.", emoji="⚠️"),
            parse_mode="HTML",
        )