"""VIP Save Partner — save partners for later, view list, unsave, direct reconnect."""
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


async def save_partner(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """Save the last partner (VIP-only)."""
    u = await get_user(uid)
    if not u:
        return False

    # ─── VIP only ───────────────────────────────────────
    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE", style="primary"),
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

    saved.append(partner_id)
    await save_user_to_db(uid, u)
    analytics["saves_today"] += 1

    # Fetch partner name for confirmation
    partner = users.get(partner_id)
    if not partner:
        partner = await get_user(partner_id)
    p_name = (partner or {}).get("name") or "User"

    # ─── Notify partner (privacy) ──────────────────────
    if SAVE_PARTNER_NOTIFY and partner:
        await safe_send(
            context, partner_id,
            box_card("Saved 💾", [
                {"type": "text",
                 "content": "💾 Someone saved you in their list!"},
                {"type": "divider"},
                {"type": "text",
                 "content": "They may reconnect with you later."},
            ], emoji="💾"),
            parse_mode="HTML",
        )

    # ─── Confirm to saver ──────────────────────────────
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 View Saved", callback_data="VIEW_SAVED", style="primary")],
    ])
    await safe_send(
        context, uid,
        box_card("Saved", [
            {"type": "text", "content": f"✅ <b>{p_name}</b> saved to your list."},
            {"type": "divider"},
            {"type": "kv", "items": [
                ("💾 Total saved", f"<b>{len(saved)}/{MAX_SAVED_PARTNERS}</b>"),
            ]},
        ], emoji="💾"),
        reply_markup=kb, parse_mode="HTML",
    )
    return True


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