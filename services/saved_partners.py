"""VIP Save Partner — save partners for later, view list, unsave, direct reconnect.

Save flow:
1. Sender taps "💾 Save Partner" → start_save_partner()
2. Partner receives allow/decline prompt
3. If allow → receiver_allow() adds sender's partner_id to sender's saved list
4. If decline → receiver_decline() just notifies sender
"""
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    MAX_SAVED_PARTNERS, SAVE_PARTNER_NOTIFY, BOT_USERNAME,
)
from state import users, analytics
from database import get_user, save_user_to_db, users_collection
from utils import box_card, box_simple, safe_send, utcnow

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# SAVE PARTNER FLOW
# ══════════════════════════════════════════════════════════════

async def start_save_partner(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """
    Sender initiates save.
    - Checks VIP, last partner, saved-list limits, partner availability.
    - Sends an allow/decline prompt to the partner.
    """
    u = await get_user(uid)
    if not u:
        return False

    # ─── VIP only ───────────────────────────────────────
    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
        ]])
        return await safe_send(
            context, uid,
            box_card("VIP Only", [
                {"type": "text", "content": "⚠️ Save Partner is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content": "Upgrade to save & reconnect with partners!"},
            ], emoji="👑"),
            reply_markup=kb, parse_mode="HTML",
        )

    partner_id = u.get("_last_partner_id")
    if not partner_id:
        return await safe_send(
            context, uid,
            box_simple("No Partner", "⚠️ No recent partner to save.", emoji="⚠️"),
            parse_mode="HTML",
        )

    saved = u.setdefault("saved_partner_ids", [])
    if partner_id in saved:
        return await safe_send(
            context, uid,
            box_simple("Already Saved",
                       "💾 This partner is already in your saved list.",
                       emoji="💾"),
            parse_mode="HTML",
        )

    if len(saved) >= MAX_SAVED_PARTNERS:
        return await safe_send(
            context, uid,
            box_card("List Full", [
                {"type": "text", "content":
                    f"📦 Saved list is full ({MAX_SAVED_PARTNERS} max)."},
                {"type": "divider"},
                {"type": "text", "content": "Use /saved to manage & remove."},
            ], emoji="📦"),
            parse_mode="HTML",
        )

    # ─── Partner checks ────────────────────────────────
    partner = users.get(partner_id)
    if not partner:
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
            box_simple("Unavailable",
                       "🚫 This user is no longer available.",
                       emoji="🚫"),
            parse_mode="HTML",
        )

    # ─── Send approval prompt to partner ───────────────
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Allow", callback_data=f"SAVE_ACCEPT:{uid}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"SAVE_DECLINE:{uid}"),
    ]])
    sent = await safe_send(
        context, partner_id,
        box_card("Save Request", [
            {"type": "text", "content":
                "💾 Someone wants to save you in their list."},
            {"type": "divider"},
            {"type": "text", "content":
                "They may reconnect with you later. You can accept or decline."},
        ], emoji="💾"),
        reply_markup=kb, parse_mode="HTML",
    )

    if not sent:
        return await safe_send(
            context, uid,
            box_simple("Failed", "⚠️ Could not reach partner.", emoji="⚠️"),
            parse_mode="HTML",
        )

    # ─── Confirm to sender ─────────────────────────────
    await safe_send(
        context, uid,
        box_simple("Request Sent",
                   "⏳ Waiting for partner to approve...",
                   emoji="⏳"),
        parse_mode="HTML",
    )
    return True


async def receiver_allow(context: ContextTypes.DEFAULT_TYPE,
                          receiver_id: int, sender_id: int) -> bool:
    """Receiver allowed being saved. Add receiver_id to sender's saved list."""
    sender = await get_user(sender_id)
    if not sender:
        return False

    saved = sender.setdefault("saved_partner_ids", [])
    if receiver_id in saved:
        return True

    if len(saved) >= MAX_SAVED_PARTNERS:
        await safe_send(
            context, sender_id,
            box_simple("List Full", f"📦 Your list is full ({MAX_SAVED_PARTNERS} max).",
                       emoji="📦"),
            parse_mode="HTML",
        )
        return False

    saved.append(receiver_id)
    await save_user_to_db(sender_id, sender)
    analytics["saves_today"] += 1

    # Notify sender
    receiver = users.get(receiver_id)
    if not receiver:
        receiver = await get_user(receiver_id)
    p_name = (receiver or {}).get("name") or "User"

    await safe_send(
        context, sender_id,
        box_card("Saved", [
            {"type": "text", "content": f"✅ <b>{p_name}</b> saved to your list."},
            {"type": "divider"},
            {"type": "kv", "items": [
                ("💾 Total saved", f"<b>{len(saved)}/{MAX_SAVED_PARTNERS}</b>"),
            ]},
        ], emoji="💾"),
        parse_mode="HTML",
    )
    logger.info(f"💾 {sender_id} saved {receiver_id}")
    return True


async def receiver_decline(context: ContextTypes.DEFAULT_TYPE,
                            receiver_id: int, sender_id: int) -> bool:
    """Receiver declined being saved. Notify sender only."""
    await safe_send(
        context, sender_id,
        box_simple("Declined",
                   "❌ Partner declined the save request.",
                   emoji="❌"),
        parse_mode="HTML",
    )
    logger.info(f"❌ {receiver_id} declined save request from {sender_id}")
    return True


# ══════════════════════════════════════════════════════════════
# LEGACY / DIRECT SAVE (without partner consent) — kept for admin use
# ══════════════════════════════════════════════════════════════

async def save_partner(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """Direct save without partner approval (admin/legacy)."""
    return await start_save_partner(context, uid)


async def unsave_partner(context: ContextTypes.DEFAULT_TYPE, uid: int,
                          partner_id: int) -> bool:
    u = await get_user(uid)
    if not u:
        return False
    saved = u.get("saved_partner_ids", [])
    if partner_id not in saved:
        return False
    saved.remove(partner_id)
    await save_user_to_db(uid, u)
    return True


async def list_saved_partners(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """Show saved list with reconnect/remove buttons."""
    u = await get_user(uid)
    if not u:
        return False

    saved_ids = u.get("saved_partner_ids", [])
    if not saved_ids:
        return await safe_send(
            context, uid,
            box_card("Saved Partners", [
                {"type": "text", "content": "📭 Your saved list is empty."},
                {"type": "divider"},
                {"type": "text", "content":
                    "💡 After a chat ends, tap '💾 Save Partner' to add them."},
            ], emoji="💾"),
            parse_mode="HTML",
        )

    lines = [f"📦 <b>Your Saved Partners</b> ({len(saved_ids)}/{MAX_SAVED_PARTNERS})", "▎"]
    buttons = []

    for i, pid in enumerate(saved_ids, 1):
        p = users.get(pid)
        if not p and users_collection is not None:
            try:
                p_doc = await users_collection.find_one(
                    {"user_id": pid}, {"name": 1, "username": 1, "is_banned": 1}
                )
                p = p_doc or {}
            except Exception:
                p = {}
        p = p or {}

        name = p.get("name") or f"User {pid}"
        uname = f"@{p['username']}" if p.get("username") else ""
        banned = "🚫" if p.get("is_banned") else ""
        online = "🟢" if pid in users and users[pid].get("state") == "IDLE" else "⚪"

        lines.append(f"▎ {i}. {online} <b>{name}</b> {uname} {banned}")
        lines.append(f"▎    <code>{pid}</code>")

    # Reconnect buttons (up to 5)
    row = []
    for pid in saved_ids[:5]:
        row.append(InlineKeyboardButton(f"🔄 {pid}", callback_data=f"RECONNECT_SAVED:{pid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Remove row
    row = []
    for pid in saved_ids[:5]:
        row.append(InlineKeyboardButton(f"❌ {pid}", callback_data=f"UNSAVE:{pid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🏠 Dashboard", callback_data="BACK_DASHBOARD")])

    await safe_send(
        context, uid, "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return True