import html
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import (
    users, queue, queue_set, queue_lock, admin_cache,
    muted_users, admin_logs, analytics, scheduled_broadcasts,
)
import state
from database import (
    is_owner_or_admin, users_collection, safe_count, resolve_user,
    save_user_to_db,
)
from utils import safe_send, utcnow
from services.vip import activate_vip
from services.matching import disconnect

logger = logging.getLogger("sparktalks")


def _sidebar(title: str, emoji: str, lines: list, tip: str = None) -> str:
    parts = [f"{emoji}  ✨  <b>{title}</b>  ✨  {emoji}", "▎"]
    parts.extend(f"▎ {l}" if l else "▎" for l in lines)
    if tip:
        parts.append("▎"); parts.append(f"💡 <i>{tip}</i>")
    return "\n".join(parts)


def _log_action(admin_id: int, action: str):
    admin_logs.append({
        "time": utcnow().strftime("%H:%M"),
        "admin": admin_id, "action": action,
    })


async def _deny(update):
    return await update.message.reply_text(
        _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"], "Admin only."),
        parse_mode="HTML",
    )


async def _not_found(update, identifier):
    return await update.message.reply_text(
        _sidebar("Not Found", "🔍", [f"User <code>{identifier}</code> not found."]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# USER MGMT (existing + verify)
# ══════════════════════════════════════════════════════════════

async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/addvip &lt;id|@user&gt; &lt;plan&gt;</code>",
                "Plans: PLAN_14D, PLAN_1M, PLAN_3M, PLAN_6M",
            ]), parse_mode="HTML",
        )
    target_id, _ = await resolve_user(context.args[0])
    plan_key = context.args[1].upper()
    if not target_id:
        return await _not_found(update, context.args[0])
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid plan key."]), parse_mode="HTML"
        )
    new_exp = await activate_vip(target_id, plan_key)
    plan = VIP_PLANS[plan_key]
    await safe_send(context, target_id, _sidebar("VIP Activated", "🎉", [
        "🎉 Your VIP is now active!",
        f"🌟 {plan['name']}",
        f"⌛ Until : {new_exp.strftime('%d %b %Y')}",
    ], "Enjoy!"), parse_mode="HTML")
    await update.message.reply_text(_sidebar("Success", "✅", [
        f"✅ Granted <b>{plan['name']}</b>",
        f"🎯 User : <code>{target_id}</code>",
    ]), parse_mode="HTML")
    _log_action(update.effective_user.id, f"granted VIP {plan_key} to {target_id}")


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/removevip &lt;id&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_vip": False, "vip_expiry_date": None,
                  "vip_tier_name": "None", "pref_gender": "Any"}},
    )
    u = users.get(target_id)
    if u:
        u["is_vip"] = False; u["vip_expiry_date"] = None
        u["vip_tier_name"] = "None"; u["pref_gender"] = "Any"
    await safe_send(context, target_id,
                    _sidebar("VIP Removed", "⌛", ["⌛ VIP removed."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ VIP removed from <code>{target_id}</code>."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"removed VIP from {target_id}")


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/verify &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"verified": True}}
    )
    u = users.get(target_id)
    if u:
        u["verified"] = True
    await update.message.reply_text(
        _sidebar("Verified", "✅", [f"✅ <code>{target_id}</code> is now verified."]),
        parse_mode="HTML",
    )


async def cmd_unverify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /unverify <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"verified": False}}
    )
    u = users.get(target_id)
    if u:
        u["verified"] = False
    await update.message.reply_text(
        _sidebar("Unverified", "✅", [f"✅ <code>{target_id}</code> unverified."]),
        parse_mode="HTML",
    )


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/warn &lt;id&gt; [reason]</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    warnings.append({"reason": reason, "by": update.effective_user.id,
                     "at": utcnow().isoformat()})
    if len(warnings) >= 3:
        await users_collection.update_one(
            {"user_id": target_id},
            {"$set": {"warnings": warnings, "is_banned": True,
                      "banned_reason": "Auto-ban: 3 warnings",
                      "banned_at": utcnow(),
                      "banned_by": update.effective_user.id}},
        )
        u = users.get(target_id)
        if u:
            u["warnings"] = warnings; u["is_banned"] = True
        await safe_send(context, target_id,
                        _sidebar("Auto-Banned", "🚫", ["🚫 You are banned! 3 warnings."]),
                        parse_mode="HTML")
        return await update.message.reply_text(
            _sidebar("Auto-Banned", "🚫",
                     [f"⚠️ 3rd warning → auto-banned <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"warnings": warnings}}
    )
    u = users.get(target_id)
    if u:
        u["warnings"] = warnings
    await safe_send(context, target_id,
                    _sidebar("Warning", "⚠️", [
                        f"⚠️ Warning #{len(warnings)}",
                        f"📝 {reason}",
                    ], "3 warnings = ban!"), parse_mode="HTML")
    await update.message.reply_text(_sidebar(
        "Warned", "⚠️", [f"⚠️ Warning #{len(warnings)} → <code>{target_id}</code>"],
    ), parse_mode="HTML")


async def cmd_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /warnings <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list) or not warnings:
        return await update.message.reply_text(_sidebar(
            "Warnings", "✅", [f"<code>{target_id}</code>: no warnings."]
        ), parse_mode="HTML")
    lines = [f"Total : {len(warnings)}/3"]
    for i, w in enumerate(warnings):
        lines.append(f"#{i+1} — {w.get('reason', '—')}")
    await update.message.reply_text(_sidebar("Warnings", "⚠️", lines), parse_mode="HTML")


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /unwarn <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list) or not warnings:
        return await update.message.reply_text("No warnings to remove.")
    warnings.pop()
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"warnings": warnings}}
    )
    u = users.get(target_id)
    if u:
        u["warnings"] = warnings
    await update.message.reply_text(_sidebar(
        "Removed", "✅", [f"✅ Last warning removed from <code>{target_id}</code>."]
    ), parse_mode="HTML")


async def cmd_resetprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /resetprofile <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"gender": None, "age": None, "country": None, "bio": None,
                  "interests": [], "pref_gender": "Any",
                  "profile_public": False, "confirm_media": True}},
    )
    u = users.get(target_id)
    if u:
        for k in ["gender", "age", "country", "bio"]:
            u[k] = None
        u["interests"] = []; u["pref_gender"] = "Any"
        u["profile_public"] = False; u["confirm_media"] = True
        u["state"] = "IDLE"; u["partner"] = None
    await safe_send(context, target_id,
                    _sidebar("Profile Reset", "🔄", ["🔄 Profile reset. Run /start."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Profile reset for <code>{target_id}</code>."]),
        parse_mode="HTML",
    )


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /resetstats <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"total_chats": 0, "total_matches": 0, "report_count": 0}},
    )
    u = users.get(target_id)
    if u:
        u["total_chats"] = 0; u["total_matches"] = 0; u["report_count"] = 0
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Stats reset for <code>{target_id}</code>."]),
        parse_mode="HTML",
    )


async def cmd_whois(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /whois <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    mem = users.get(target_id, {})
    name = doc.get("name") or "—"
    uname = f"@{doc.get('username')}" if doc.get("username") else "—"
    status = f"👑 {doc.get('vip_tier_name', 'VIP')}" if doc.get("is_vip") else "⚪ Free"
    warnings = doc.get("warnings", [])
    w_count = len(warnings) if isinstance(warnings, list) else 0
    state_str = "🟢 In Chat" if mem.get("partner") else (
        "🟡 Searching" if mem.get("state") == "SEARCHING" else "⚪ Idle"
    )
    await update.message.reply_text(_sidebar(
        "Quick Lookup", "🔍", [
            f"👤 <b>{name}</b> ({uname})",
            f"🆔 <code>{target_id}</code>",
            f"{status}",
            f"💬 Chats : {doc.get('total_chats', 0)}",
            f"🏆 Matches : {doc.get('total_matches', 0)}",
            f"⚠️ Warnings : {w_count}/3",
            f"🚫 Banned : {'Yes' if doc.get('is_banned') else 'No'}",
            f"🟢 Now : {state_str}",
        ]
    ), parse_mode="HTML")


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /userinfo <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    mem = users.get(target_id, {})
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y") if exp else "—"
    name = doc.get("name") or "Unknown"
    uname = f"@{doc.get('username')}" if doc.get("username") else "—"
    status = f"👑 {doc.get('vip_tier_name', 'VIP')}" if doc.get("is_vip") else "⚪ Free"
    warnings = doc.get("warnings", [])
    w_count = len(warnings) if isinstance(warnings, list) else 0
    blocked = doc.get("blocked_users", []) or []
    state_line = "🟢 In Chat" if mem.get("partner") else (
        "🟡 Searching" if mem.get("state") == "SEARCHING" else "⚪ Idle"
    )
    lines = [
        "🆔  <b>Identity</b>",
        f"  ├ <code>{target_id}</code>",
        f"  ├ {name} | {uname}",
        f"  ├ {doc.get('gender') or '—'} | 🎂 {doc.get('age') or '—'}",
        f"  ├ 🌍 {doc.get('country') or '—'}",
        f"  └ 🏷️ {', '.join(doc.get('interests') or []) or 'None'}",
        "",
        "⭐  <b>Status</b>",
        f"  ├ {status}",
        f"  ├ ⌛ {exp_str}",
        f"  ├ 🚫 Banned: {'Yes' if doc.get('is_banned') else 'No'}",
        f"  ├ ⚠️ Warnings: {w_count}/3",
        f"  ├ ✅ Verified: {'Yes' if doc.get('verified') else 'No'}",
        f"  └ 🛡️ Admin: {'Yes' if doc.get('is_admin') else 'No'}",
        "",
        "📊  <b>Activity</b>",
        f"  ├ 💬 Chats: {doc.get('total_chats', 0)}",
        f"  ├ 🏆 Matches: {doc.get('total_matches', 0)}",
        f"  ├ 🚫 Blocked: {len(blocked)}",
        f"  └ 🚨 Reports: {doc.get('report_count', 0)}",
        "",
        "⚡  <b>Runtime</b>",
        f"  ├ State: {state_line}",
        f"  └ Partner: {mem.get('partner') or 'None'}",
    ]
    await update.message.reply_text(_sidebar("User Info", "👤", lines), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# MODERATION
# ══════════════════════════════════════════════════════════════

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /ban <id> [reason]")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_banned": True, "banned_reason": reason,
                  "banned_at": utcnow(),
                  "banned_by": update.effective_user.id}},
    )
    admin_cache.discard(target_id)
    u = users.get(target_id)
    if u:
        u["is_banned"] = True
        if u.get("partner"):
            await disconnect(context, target_id, u["partner"], ender_id=target_id)
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
    await safe_send(context, target_id,
                    _sidebar("Banned", "🚫", [f"🚫 You are banned! {reason}"]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Banned <code>{target_id}</code>"]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"banned {target_id}: {reason}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /unban <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_banned": False, "banned_reason": None}},
    )
    u = users.get(target_id)
    if u:
        u["is_banned"] = False
    if doc.get("is_admin"):
        admin_cache.add(target_id)
    await safe_send(context, target_id,
                    _sidebar("Unbanned", "✅", ["✅ Unbanned. Welcome back!"]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ <code>{target_id}</code> unbanned."]),
        parse_mode="HTML",
    )


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /kick <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    u = users.get(target_id)
    if not u:
        return await update.message.reply_text(f"User offline.")
    kicked_from = "idle"
    if u.get("partner"):
        await disconnect(context, target_id, u["partner"], ender_id=target_id)
        kicked_from = "chat"
    elif u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
        kicked_from = "search"
    await safe_send(context, target_id,
                    _sidebar("Kicked", "👢", ["👢 Kicked by admin."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅",
                 [f"✅ Kicked <code>{target_id}</code> from {kicked_from}"]),
        parse_mode="HTML",
    )


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /mute <id> [minutes]")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    try:
        minutes = int(context.args[1]) if len(context.args) > 1 else 10
    except ValueError:
        minutes = 10
    unmute_at = utcnow() + timedelta(minutes=minutes)
    muted_users[target_id] = unmute_at.timestamp()
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"muted_until": unmute_at}},
    )
    await safe_send(context, target_id,
                    _sidebar("Muted", "🔇", [f"🔇 Muted for {minutes} min."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Muted <code>{target_id}</code> for {minutes}m"]),
        parse_mode="HTML",
    )


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /unmute <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    muted_users.pop(target_id, None)
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"muted_until": None}},
    )
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Unmuted <code>{target_id}</code>"]),
        parse_mode="HTML",
    )


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /forceend <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    u = users.get(target_id)
    if not u:
        return await update.message.reply_text("User offline.")
    if u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
        return await update.message.reply_text(
            _sidebar("Done", "✅", [f"✅ Search cancelled for <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text("Not in a chat.")
    await disconnect(context, target_id, partner, ender_id=target_id)
    await update.message.reply_text(
        _sidebar("Done", "✅",
                 [f"✅ Ended <code>{target_id}</code> & <code>{partner}</code>"]),
        parse_mode="HTML",
    )


async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = []
    if users_collection is not None:
        async for doc in users_collection.find(
            {"is_banned": True},
            {"user_id": 1, "name": 1, "username": 1, "banned_reason": 1},
        ).limit(50):
            name = doc.get("name") or "—"
            uname = f"@{doc['username']}" if doc.get("username") else "—"
            reason = doc.get("banned_reason") or "—"
            lines.append(f"• <code>{doc['user_id']}</code> {name} ({uname})")
            lines.append(f"  └ {reason}")
    if not lines:
        lines = ["✅ No banned users."]
    await update.message.reply_text(
        _sidebar("Ban List", "🚫", lines), parse_mode="HTML"
    )


async def cmd_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /blocked <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    blocked_ids = doc.get("blocked_users", []) or []
    if not blocked_ids:
        return await update.message.reply_text(
            f"✅ No blocks for <code>{target_id}</code>.", parse_mode="HTML"
        )
    lines = [f"Total blocked: {len(blocked_ids)}"]
    async for b in users_collection.find(
        {"user_id": {"$in": blocked_ids}},
        {"user_id": 1, "name": 1, "username": 1},
    ):
        name = b.get("name") or "Unknown"
        uname = f"@{b.get('username')}" if b.get("username") else "—"
        lines.append(f"• <code>{b['user_id']}</code> {name} ({uname})")
    await update.message.reply_text(
        _sidebar("Blocked Users", "🚫", lines), parse_mode="HTML"
    )


async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /unblock <id> <blocked_id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    blocked_id, _ = await resolve_user(context.args[1])
    if not blocked_id:
        return await _not_found(update, context.args[1])
    blocked = doc.get("blocked_users", []) or []
    if blocked_id not in blocked:
        return await update.message.reply_text(
            "⚠️ Not in block list.", parse_mode="HTML"
        )
    new_list = [b for b in blocked if b != blocked_id]
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"blocked_users": new_list}}
    )
    u = users.get(target_id)
    if u:
        u["blocked_users"] = new_list
    await update.message.reply_text(
        _sidebar("Unblocked", "🔓", [f"✅ Unblocked <code>{blocked_id}</code>"]),
        parse_mode="HTML",
    )


async def cmd_clearblocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /clearblocks <id>")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    count = len(doc.get("blocked_users", []) or [])
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"blocked_users": []}}
    )
    u = users.get(target_id)
    if u:
        u["blocked_users"] = []
    await update.message.reply_text(
        _sidebar("Cleared", "🔓", [f"✅ Cleared {count} blocks for <code>{target_id}</code>"]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    total = await safe_count()
    vip = await safe_count({"is_vip": True})
    banned = await safe_count({"is_banned": True})
    admins = await safe_count({"is_admin": True})
    active = sum(1 for u in users.values() if u.get("state") == "CHAT") // 2
    searching = sum(1 for u in users.values() if u.get("state") == "SEARCHING")

    await update.message.reply_text(_sidebar(
        "Stats", "📊",
        ["👥 <b>Users</b>",
         f"  ├ Total : <b>{total}</b>",
         f"  ├ ⭐ VIP : <b>{vip}</b>",
         f"  ├ 🚫 Banned : <b>{banned}</b>",
         f"  └ 🛡️ Admins : <b>{admins}</b>",
         "",
         "🟢 <b>Live</b>",
         f"  ├ Online : <b>{len(users)}</b>",
         f"  ├ 💬 Chats : <b>{active}</b>",
         f"  ├ 🔍 Searching : <b>{searching}</b>",
         f"  └ 📋 Queue : <b>{len(queue)}</b>",
         "",
         "📅 <b>Today</b>",
         f"  ├ 💞 Matches : {analytics['matches_today']}",
         f"  ├ 🚨 Reports : {analytics['reports_today']}",
         f"  ├ ⚠️ Violations : {analytics['violations_today']}",
         f"  ├ 🔇 Mutes : {analytics['mutes_today']}",
         f"  ├ 🎁 Referrals : {analytics['referrals_today']}",
         f"  ├ 🎙️ Voice rooms : {analytics['voice_rooms_today']}",
         f"  └ 💰 VIP purchases : {analytics['vip_purchases_today']}"],
    ), parse_mode="HTML")


async def cmd_topusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    rank = 1
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    async for doc in users_collection.find(
        {"is_banned": False}, {"user_id": 1, "name": 1, "total_chats": 1}
    ).sort("total_chats", -1).limit(10):
        medal = medals.get(rank, f"{rank}.")
        lines.append(f"  {medal} {doc.get('name') or '—'} — {doc.get('total_chats', 0)} chats")
        rank += 1
    if not lines:
        lines = ["No data."]
    await update.message.reply_text(
        _sidebar("Top Users", "🏆", lines), parse_mode="HTML"
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    async for doc in users_collection.find(
        {"is_banned": False}, {"user_id": 1, "name": 1, "joined_date": 1}
    ).sort("joined_date", -1).limit(10):
        name = doc.get("name") or "—"
        jd = doc.get("joined_date")
        jd_str = jd.strftime("%d %b") if jd else "—"
        lines.append(f"  • <code>{doc['user_id']}</code> {name} ({jd_str})")
    if not lines:
        lines = ["No recent users."]
    await update.message.reply_text(
        _sidebar("Recent Users", "🆕", lines), parse_mode="HTML"
    )


async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    count = 0
    async for doc in users_collection.find(
        {"is_vip": True},
        {"user_id": 1, "name": 1, "vip_tier_name": 1, "vip_expiry_date": 1},
    ).limit(30):
        name = doc.get("name") or "—"
        tier = doc.get("vip_tier_name") or "VIP"
        exp = doc.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b") if exp else "—"
        lines.append(f"  • <code>{doc['user_id']}</code> {name} · 👑 {tier} · ⌛ {exp_str}")
        count += 1
    if not lines:
        lines = ["✅ No VIP users yet."]
    await update.message.reply_text(
        _sidebar(f"VIP Users ({count})", "👑", lines), parse_mode="HTML"
    )


async def cmd_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = [f"Queue size : {len(queue)}", ""]
    for i, uid_ in enumerate(list(queue)[:10]):
        u = users.get(uid_)
        name = u.get("name") if u else "?"
        lines.append(f"  • <code>{uid_}</code> — {name}")
    await update.message.reply_text(
        _sidebar("Queue", "🔍", lines), parse_mode="HTML"
    )


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    chats = []
    seen = set()
    for uid, u in users.items():
        if u.get("state") == "CHAT" and u.get("partner") and uid not in seen:
            pid = u["partner"]
            seen.add(uid); seen.add(pid)
            u1 = users.get(uid, {})
            u2 = users.get(pid, {})
            chats.append(f"  • <code>{uid}</code> ↔ <code>{pid}</code>")
    if not chats:
        chats = ["✅ No active chats."]
    await update.message.reply_text(
        _sidebar(f"Active Chats ({len(chats)})", "💬", chats), parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════
# COMMUNICATION
# ══════════════════════════════════════════════════════════════

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    parts = (update.message.text or "").split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/broadcast &lt;message&gt;</code>"]),
            parse_mode="HTML",
        )
    message = parts[1].strip()
    context.user_data["pending_broadcast"] = message
    preview = _sidebar("Confirm Broadcast", "📢", [
        "📢 Preview:", "", message, "",
        "⚠️ Will be sent to ALL users (excluding banned).",
    ])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send", callback_data="BC_CONFIRM"),
        InlineKeyboardButton("❌ Cancel", callback_data="BC_CANCEL"),
    ]])
    await update.message.reply_text(preview, reply_markup=kb, parse_mode="HTML")


async def cmd_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not update.message.reply_to_message:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Reply to a message to broadcast it."]),
            parse_mode="HTML",
        )
    src = update.message.reply_to_message
    await update.message.reply_text("📢 Broadcasting media...")
    from services.broadcast import execute_media_broadcast
    sent, failed = await execute_media_broadcast(context, src.chat_id, src.message_id)
    await update.message.reply_text(
        _sidebar("Broadcast Done", "📢",
                 [f"✅ Sent: {sent}", f"❌ Failed: {failed}"]),
        parse_mode="HTML",
    )


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /dm <id> <msg>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    message = " ".join(context.args[1:])
    result = await safe_send(context, target_id,
                             _sidebar("Message", "📩", [message, "", "— SparkTalks Team"]),
                             parse_mode="HTML")
    if result:
        await update.message.reply_text(
            _sidebar("Sent", "✅", [f"✅ Delivered to <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            _sidebar("Failed", "❌", [f"❌ Could not deliver."]), parse_mode="HTML"
        )


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /notify <id> <msg>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    message = " ".join(context.args[1:])
    await safe_send(context, target_id, f"🔔 <i>{message}</i>", parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Sent", "🔔", [f"✅ Notified <code>{target_id}</code>"]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# SCHEDULED BROADCASTS
# ══════════════════════════════════════════════════════════════

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage: /schedule <minutes_from_now> <message>
    Or: reply to a media message with /schedule <minutes_from_now>
    """
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Schedule Broadcast", "⏰", [
                "Usage:",
                "<code>/schedule &lt;minutes&gt; &lt;message&gt;</code>",
                "Or reply to a message:",
                "<code>/schedule &lt;minutes&gt;</code>",
            ]), parse_mode="HTML",
        )
    try:
        minutes = int(context.args[0])
        if minutes < 1 or minutes > 60 * 24 * 7:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("⚠️ Minutes must be 1–10080 (7 days).")

    from services.scheduler import schedule_broadcast
    run_at = utcnow() + timedelta(minutes=minutes)

    if update.message.reply_to_message:
        src = update.message.reply_to_message
        sid = await schedule_broadcast(
            run_at, media={"chat_id": src.chat_id, "message_id": src.message_id},
            created_by=update.effective_user.id,
        )
    else:
        text = " ".join(context.args[1:])
        if not text.strip():
            return await update.message.reply_text("⚠️ No message provided.")
        sid = await schedule_broadcast(
            run_at, message=text, created_by=update.effective_user.id,
        )
    await update.message.reply_text(
        _sidebar("Scheduled", "⏰", [
            f"✅ Scheduled broadcast",
            f"⏰ Runs at: {run_at.strftime('%d %b %H:%M')} UTC",
            f"🆔 ID: <code>{sid}</code>",
        ]), parse_mode="HTML",
    )


async def cmd_scheduled_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not scheduled_broadcasts:
        return await update.message.reply_text("📭 No scheduled broadcasts.")
    lines = [f"Total: {len(scheduled_broadcasts)}", ""]
    for s in scheduled_broadcasts:
        preview = (s.get("message") or "[media]")[:40]
        lines.append(f"• <code>{s['id']}</code>")
        lines.append(f"  ⏰ {s['run_at'].strftime('%d %b %H:%M')} — {preview}")
    await update.message.reply_text(
        _sidebar("Scheduled", "⏰", lines), parse_mode="HTML"
    )


async def cmd_scheduled_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /unschedule <id>")
    from services.scheduler import cancel_scheduled
    ok = await cancel_scheduled(context.args[0])
    if ok:
        await update.message.reply_text("✅ Cancelled.")
    else:
        await update.message.reply_text("⚠️ Not found.")


# ══════════════════════════════════════════════════════════════
# ADMIN MANAGEMENT
# ══════════════════════════════════════════════════════════════

async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not context.args:
        return await update.message.reply_text("Usage: /setadmin <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    if target_id == OWNER_ID:
        return await update.message.reply_text("Owner already admin.")
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"is_admin": True}},
    )
    admin_cache.add(target_id)
    u = users.get(target_id)
    if u:
        u["is_admin"] = True
    await safe_send(context, target_id,
                    _sidebar("Admin Granted", "🛡️", ["🛡️ You are now an Admin."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ <code>{target_id}</code> is now Admin."]),
        parse_mode="HTML",
    )


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not context.args:
        return await update.message.reply_text("Usage: /removeadmin <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    if target_id == OWNER_ID:
        return await update.message.reply_text("Cannot remove Owner.")
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"is_admin": False}},
    )
    admin_cache.discard(target_id)
    u = users.get(target_id)
    if u:
        u["is_admin"] = False
    await safe_send(context, target_id,
                    _sidebar("Admin Removed", "🛡️", ["🛡️ Admin revoked."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Admin removed from <code>{target_id}</code>."]),
        parse_mode="HTML",
    )


async def cmd_adminlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = ["👑 <b>Owner</b>", f"  └ <code>{OWNER_ID}</code>"]
    others = [a for a in admin_cache if a != OWNER_ID]
    if others:
        lines.append("")
        lines.append("🛡️ <b>Admins</b>")
        for i, aid in enumerate(others):
            u = users.get(aid)
            name = u.get("name") if u else "—"
            lines.append(f"  • <code>{aid}</code> — {name}")
    await update.message.reply_text(
        _sidebar("Admins", "🛡️", lines), parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════
# SYSTEM
# ══════════════════════════════════════════════════════════════

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    mem_str = "N/A"
    try:
        import psutil
        proc = psutil.Process()
        mem_str = f"{proc.memory_info().rss / 1024 / 1024:.1f} MB"
    except Exception:
        pass
    db_ok = "✅ OK" if users_collection is not None else "❌ Down"
    maint = "🚧 ON" if state.maintenance_mode else "✅ OFF"
    uptime = "—"
    if analytics.get("start_time"):
        delta = utcnow() - analytics["start_time"]
        uptime = str(delta).split(".")[0]
    await update.message.reply_text(_sidebar(
        "System Health", "💚",
        ["🖥️ <b>Bot</b>",
         f"  ├ Memory : {mem_str}",
         f"  └ Uptime : {uptime}",
         "",
         "🗄️ <b>Database</b>",
         f"  ├ Connection : {db_ok}",
         f"  └ Users cached : {len(users)}",
         "",
         "📋 <b>Runtime</b>",
         f"  ├ Queue : {len(queue)}",
         f"  ├ Admins : {len(admin_cache)}",
         f"  ├ Muted : {len(muted_users)}",
         f"  ├ Scheduled : {len(scheduled_broadcasts)}",
         f"  └ Maintenance : {maint}"],
    ), parse_mode="HTML")


async def cmd_clearchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /clearchat <id>")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    u = users.get(target_id)
    if not u:
        return await update.message.reply_text("User offline.")
    if u.get("partner"):
        pu = users.get(u["partner"])
        if pu:
            pu["partner"] = None; pu["state"] = "IDLE"
    u["partner"] = None; u["state"] = "IDLE"; u["pending_media"] = {}
    async with queue_lock:
        try:
            queue.remove(target_id)
        except ValueError:
            pass
        queue_set.discard(target_id)
    await update.message.reply_text("✅ State cleared.")


async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not context.args:
        status = "🚧 ON" if state.maintenance_mode else "✅ OFF"
        return await update.message.reply_text(
            _sidebar("Maintenance", "🚧", [
                f"Current: {status}",
                "Usage: /maintenance on|off",
            ]), parse_mode="HTML",
        )
    arg = context.args[0].lower()
    if arg in ("on", "true", "1"):
        state.maintenance_mode = True
        await update.message.reply_text("🚧 Maintenance ON.")
    elif arg in ("off", "false", "0"):
        state.maintenance_mode = False
        await update.message.reply_text("✅ Maintenance OFF.")


async def cmd_clearcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    from state import last_next_time, message_reactions_map, message_edit_map
    cleared = len(users)
    users.clear()
    last_next_time.clear()
    message_reactions_map.clear()
    message_edit_map.clear()
    muted_users.clear()
    await update.message.reply_text(
        _sidebar("Cache Cleared", "🧹", [f"✅ Cleared {cleared} users."]),
        parse_mode="HTML",
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    count = 10
    if context.args:
        try:
            count = min(int(context.args[0]), 50)
        except ValueError:
            pass
    if not admin_logs:
        return await update.message.reply_text("📭 No logs yet.")
    lines = [f"Last {min(count, len(admin_logs))} actions", ""]
    for entry in list(admin_logs)[-count:]:
        lines.append(f"[{entry['time']}] <code>{entry['admin']}</code>")
        lines.append(f"  └ {entry['action']}")
    await update.message.reply_text(
        _sidebar("Admin Logs", "📝", lines), parse_mode="HTML"
    )