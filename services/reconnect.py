"""VIP Reconnect — reconnect with the last partner directly, bypassing search."""
import logging
from datetime import datetime

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


async def reconnect_with_partner(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """
    Reconnect with the last partner (uid must be VIP, IDLE, and have _last_partner_id).
    Bypasses searching — direct connect.
    """
    u = await get_user(uid)
    if not u:
        return False

    # ─── Must be VIP ────────────────────────────────────
    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE", style="primary"),
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

    # ─── Not currently busy ────────────────────────────
    if u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Busy", "⚠️ You're already in a chat. Use /end first.", emoji="⚠️"),
            parse_mode="HTML",
        )

    partner_id = u.get("_last_partner_id")
    if not partner_id:
        return await safe_send(
            context, uid,
            box_simple("No Recent Partner",
                       "⚠️ You haven't chatted with anyone recently.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    # ─── Daily limit ────────────────────────────────────
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

    # ─── Partner checks ────────────────────────────────
    partner = users.get(partner_id)
    if not partner:
        # Try DB
        partner = await get_user(partner_id)
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

    if partner.get("partner"):
        return await safe_send(context, uid, RECONNECT_BUSY_MSG, parse_mode="HTML")

    if partner.get("state") == "SEARCHING":
        return await safe_send(context, uid, RECONNECT_BUSY_MSG, parse_mode="HTML")

    # Blocked check (both directions)
    if partner_id in (u.get("blocked_users") or []):
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

    # ─── Remove both from any queue just in case ───────
    async with queue_lock:
        for pid in (uid, partner_id):
            try:
                from state import queue
                queue.remove(pid)
            except Exception:
                pass
            queue_set.discard(pid)

    # ─── Perform direct connect ────────────────────────
    from services.matching import connect_users
    try:
        await connect_users(context, uid, partner_id)
        _increment_reconnect(uid)
        analytics["reconnects_today"] += 1
        logger.info(f"Reconnect: {uid} ↔ {partner_id}")
        return True
    except Exception as e:
        logger.error(f"Reconnect failed: {e}", exc_info=True)
        return await safe_send(
            context, uid,
            box_simple("Failed", "⚠️ Reconnect failed. Try again.", emoji="⚠️"),
            parse_mode="HTML",
        )