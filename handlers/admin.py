import html
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommandScopeChat,
)
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
from utils import safe_send, utcnow, esc
from services.vip import activate_vip
from services.matching import disconnect
from bot_commands import get_admin_commands

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


async def _require_db(update) -> bool:
    """Guard: skip command if DB is down. Returns True if DB is up."""
    if users_collection is None:
        try:
            await update.message.reply_text(
                _sidebar("DB Down", "❌", ["⚠️ Database not connected."]),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False
    return True


async def _not_found(update, identifier):
    return await update.message.reply_text(
        _sidebar("Not Found", "🔍", [f"User <code>{identifier}</code> not found."]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# USER MGMT
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

    plan = VIP_PLANS[plan_key]

    new_exp = await activate_vip(
        target_id, plan_key,
        source="admin",
        granted_by=update.effective_user.id,
        note=f"Admin grant",
    )

    if not new_exp:
        return await update.message.reply_text(
            _sidebar("Failed", "❌", [
                "❌ VIP activation failed!",
                f"🎯 User : <code>{target_id}</code>",
                f"📦 Plan : {plan['name']}",
                "",
                "💡 Check DB / logs.",
            ]), parse_mode="HTML",
        )

    if OWNER_ID:
        try:
            target_u = await resolve_user(str(target_id))
            name_t = esc((target_u[1] or {}).get("name") or "Unknown") if target_u[1] else "Unknown"
            uname_t = (target_u[1] or {}).get("username") if target_u[1] else None
            uname_str_t = esc(f"@{uname_t}") if uname_t else "—"
        except Exception:
            name_t = "Unknown"
            uname_str_t = "—"

        notify_body = (
            f"🎁  ✨  <b>VIP Granted by Admin</b>  ✨  🎁\n"
            f"▎\n"
            f"▎ 👤 User : <b>{name_t}</b>\n"
            f"▎ 🆔 ID : <code>{target_id}</code>\n"
            f"▎ 🔗 Username : {uname_str_t}\n"
            f"▎\n"
            f"▎ 🎁 Plan : <b>{esc(plan['name'])}</b>\n"
            f"▎ 📦 Days : <b>+{plan['days']}</b>\n"
            f"▎ ⌛ Until : <b>{new_exp.strftime('%d %b %Y')}</b>\n"
            f"▎ 🛡️ Admin : <code>{update.effective_user.id}</code>\n"
            f"▎\n"
            f"▎ 🕐 {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        await safe_send(context, OWNER_ID, notify_body, parse_mode="HTML")

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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
        lines.append(f"#{i+1} — {esc(w.get('reason', '—'))}")
    await update.message.reply_text(_sidebar("Warnings", "⚠️", lines), parse_mode="HTML")


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
    name = esc(doc.get("name") or "—")
    uname = esc(f"@{doc.get('username')}" if doc.get("username") else "—")
    status = f"👑 {esc(doc.get('vip_tier_name', 'VIP'))}" if doc.get("is_vip") else "⚪ Free"
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
    name = esc(doc.get("name") or "Unknown")
    uname = esc(f"@{doc.get('username')}" if doc.get("username") else "—")
    status = f"👑 {esc(doc.get('vip_tier_name', 'VIP'))}" if doc.get("is_vip") else "⚪ Free"
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
            name = esc(doc.get("name") or "—")
            uname = esc(f"@{doc['username']}" if doc.get("username") else "—")
            reason = esc(doc.get("banned_reason") or "—")
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
    if users_collection is not None:
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
    if not await _require_db(update):
        return
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
    if not await _require_db(update):
        return
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
        lines.append(f"  {medal} {esc(doc.get('name') or '—')} — {doc.get('total_chats', 0)} chats")
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
        name = esc(doc.get("name") or "—")
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
        name = esc(doc.get("name") or "—")
        tier = esc(doc.get("vip_tier_name") or "VIP")
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
        name = esc(u.get("name") if u else "?")
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
    sent, failed = await execute_media_broadcast(
        context, src.chat_id, src.message_id,
        admin_uid=update.effective_user.id,
    )
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
    if not await _require_db(update):
        return
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

    # ⭐ Push admin command scope immediately (no restart needed)
    try:
        await context.bot.set_my_commands(
            get_admin_commands(),
            scope=BotCommandScopeChat(chat_id=target_id),
        )
    except Exception as e:
        logger.warning(f"Could not push admin commands to {target_id}: {e}")

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
    if not await _require_db(update):
        return
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

    # ⭐ Reset commands to normal scope
    try:
        from bot_commands import get_user_commands
        await context.bot.set_my_commands(
            get_user_commands(),
            scope=BotCommandScopeChat(chat_id=target_id),
        )
    except Exception as e:
        logger.warning(f"Could not reset commands for {target_id}: {e}")

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
            lines.append(f"  • <code>{aid}</code> — {esc(name)}")
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


# ══════════════════════════════════════════════════════════════
# FORCE DELETE
# ══════════════════════════════════════════════════════════════

async def cmd_forcedelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not context.args:
        return await update.message.reply_text(
            _sidebar("Force Delete", "🗑️", [
                "📌 <b>Usage:</b>",
                "<code>/forcedelete &lt;user_id&gt;</code>",
                "",
                "⚠️ <b>This will permanently delete:</b>",
                "  ├ Profile + stats",
                "  ├ VIP status",
                "  ├ Saved partners",
                "  ├ Achievements",
                "  ├ Chat history",
                "  ├ Queue position",
                "  ├ Voice rooms",
                "  └ All caches",
                "",
                "❗ Cannot be undone!",
            ], tip="Use /userinfo <id> to confirm first"),
            parse_mode="HTML",
        )

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Invalid user ID.",
                f"Got: <code>{context.args[0]}</code>",
            ]), parse_mode="HTML",
        )

    if target_id == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", ["Cannot delete the Owner account!"]),
            parse_mode="HTML",
        )

    if target_id == update.effective_user.id:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", [
                "You cannot delete yourself!",
                "Use /delete_me instead (self-service).",
            ]), parse_mode="HTML",
        )

    in_memory = target_id in users
    in_db = False
    if users_collection is not None:
        try:
            doc = await users_collection.find_one({"user_id": target_id}, {"_id": 1})
            in_db = doc is not None
        except Exception:
            pass

    if not in_memory and not in_db:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [
                f"User <code>{target_id}</code> does not exist",
                "in memory or database.",
            ]), parse_mode="HTML",
        )

    context.user_data["forcedelete_target"] = target_id

    where = []
    if in_memory:
        where.append("✅ Memory")
    if in_db:
        where.append("✅ Database")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🗑️ Yes, Delete Permanently",
            callback_data=f"FD_CONFIRM:{target_id}",
        ),
    ], [
        InlineKeyboardButton("❌ Cancel", callback_data="FD_CANCEL"),
    ]])

    await update.message.reply_text(
        _sidebar("Confirm Force Delete", "⚠️", [
            f"🆔 User : <code>{target_id}</code>",
            f"📦 Found in : {' · '.join(where)}",
            "",
            "⚠️ <b>This action is irreversible!</b>",
            "All data will be permanently removed.",
        ], tip="Tap confirm below to proceed"),
        reply_markup=kb, parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# PURGE USER
# ══════════════════════════════════════════════════════════════

async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not context.args:
        return await update.message.reply_text(
            _sidebar("Purge User", "🧨", [
                "📌 <b>Usage:</b>",
                "<code>/purge &lt;user_id&gt;</code>",
                "",
                "⚠️ <b>Immediately deletes:</b>",
                "  ├ Profile + stats",
                "  ├ VIP status",
                "  ├ Saved partners",
                "  ├ Blocked users",
                "  ├ Achievements",
                "  ├ Queue position",
                "  ├ Chat sessions",
                "  ├ Voice rooms",
                "  └ All caches",
                "",
                "🎯 <b>Result:</b>",
                "  └ User looks brand new next time",
                "     (gender setup required)",
                "",
                "❗ No confirmation — be careful!",
            ], tip="For confirmation flow, use /forcedelete"),
            parse_mode="HTML",
        )

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Invalid user ID.",
                f"Got: <code>{context.args[0]}</code>",
            ]), parse_mode="HTML",
        )

    if target_id == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", ["Cannot purge the Owner account!"]),
            parse_mode="HTML",
        )

    if target_id == update.effective_user.id:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", [
                "You cannot purge yourself!",
                "Use /delete_me instead.",
            ]), parse_mode="HTML",
        )

    progress_msg = await update.message.reply_text(
        _sidebar("Purging...", "⏳", [
            f"🆔 User : <code>{target_id}</code>",
            "",
            "⏳ Removing all data...",
        ]),
        parse_mode="HTML",
    )

    try:
        from services.user_deletion import delete_user_completely
        stats = await asyncio.wait_for(
            delete_user_completely(target_id),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Purge timed out for {target_id}")
        stats = {
            "was_in_memory": True, "was_in_queue": False, "was_in_db": False,
            "partner_notified": False, "db_deleted": False,
            "memory_cleaned": True, "refs_cleaned": 0,
            "error": "Timed out — memory cleared, DB retry in background",
        }
    except Exception as e:
        logger.error(f"Purge error for {target_id}: {e}", exc_info=True)
        stats = {
            "was_in_memory": False, "was_in_queue": False, "was_in_db": False,
            "partner_notified": False, "db_deleted": False,
            "memory_cleaned": False, "refs_cleaned": 0, "error": str(e),
        }

    lines = [
        f"🆔 User : <code>{target_id}</code>",
        "",
        "📊 <b>Purge Summary</b>",
        f"  ├ Memory : {'✅ Cleared' if stats.get('memory_cleaned') else '❌ Failed'}",
        f"  ├ Queue : {'✅ Removed' if stats.get('was_in_queue') else '⚪ Not in queue'}",
        f"  ├ Database : {'✅ Deleted' if stats.get('db_deleted') else '⚪ Not found'}",
        f"  ├ Partner : {'✅ Cleared' if stats.get('partner_notified') else '⚪ No partner'}",
        f"  └ Refs : <b>{stats.get('refs_cleaned', 0)}</b> cleaned",
        "",
        "🎯 <b>Next Time User /start</b>",
        "  └ ✅ Fresh setup (gender will be asked)",
    ]

    if stats.get("error"):
        lines.append("")
        lines.append(f"⚠️ Note: <code>{stats['error']}</code>")

    try:
        await progress_msg.edit_text(
            _sidebar("Purged", "🧨", lines), parse_mode="HTML",
        )
    except Exception:
        await update.message.reply_text(
            _sidebar("Purged", "🧨", lines), parse_mode="HTML",
        )

    _log_action(
        update.effective_user.id,
        f"purged user {target_id} (refs_cleaned={stats.get('refs_cleaned', 0)})"
    )
    logger.info(f"🧨 Admin {update.effective_user.id} purged user {target_id}")


# ══════════════════════════════════════════════════════════════
# GET FILE ID
# ══════════════════════════════════════════════════════════════

async def cmd_getfileid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    msg = update.message
    reply = msg.reply_to_message

    if not reply or not reply.photo:
        return await msg.reply_text(
            _sidebar("Get File ID", "🆔", [
                "📌 <b>How to use:</b>",
                "  1. Send a photo to this bot",
                "  2. Reply to that photo with",
                "     <code>/getfileid</code>",
                "  3. Copy the ID shown",
                "  4. Add to Render env vars:",
                "     <code>WELCOME_IMAGE_FILE_ID=...</code>",
                "     <code>DASHBOARD_IMAGE_FILE_ID=...</code>",
            ]),
            parse_mode="HTML",
        )

    file_id = reply.photo[-1].file_id

    await msg.reply_text(
        _sidebar("File ID Captured", "🆔", [
            "📋 <b>Your File ID:</b>",
            "",
            f"<code>{file_id}</code>",
            "",
            "📌 <b>Next steps:</b>",
            "  ├ Copy the ID above",
            "  ├ Render → Environment",
            "  ├ Add:",
            "  │  <code>WELCOME_IMAGE_FILE_ID=...</code>",
            "  │  <code>DASHBOARD_IMAGE_FILE_ID=...</code>",
            "  └ Save → Redeploy",
            "",
            "💡 <i>Banner will auto-appear after redeploy.</i>",
        ], tip="Copy the ID exactly — no spaces!"),
        parse_mode="HTML",
    )

    logger.info(f"🆔 Admin {update.effective_user.id} requested file_id")


# ══════════════════════════════════════════════════════════════
# BAN APPEALS
# ══════════════════════════════════════════════════════════════

async def cmd_appeals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    lines = ["Recent banned users:", ""]
    count = 0
    if users_collection is not None:
        async for doc in users_collection.find(
            {"is_banned": True},
            {"user_id": 1, "name": 1, "banned_reason": 1, "banned_at": 1},
        ).sort("banned_at", -1).limit(10):
            name = esc(doc.get("name") or "—")
            reason = esc(doc.get("banned_reason") or "—")
            at = doc.get("banned_at")
            at_str = at.strftime("%d %b %H:%M") if at else "—"
            lines.append(f"• <code>{doc['user_id']}</code> {name}")
            lines.append(f"  └ {reason} ({at_str})")
            count += 1

    if count == 0:
        lines = ["✅ No banned users currently."]

    await update.message.reply_text(
        _sidebar("Appeals / Bans", "⚖️", lines,
                 tip="Use /unban <id> to approve appeals."),
        parse_mode="HTML",
    )


async def cmd_resolve_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not await _require_db(update):
        return

    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Usage", "⚠️", [
                "<code>/resolve &lt;id&gt; approve</code>",
                "<code>/resolve &lt;id&gt; reject</code>",
            ]), parse_mode="HTML",
        )

    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    action = context.args[1].lower()

    if action in ("approve", "unban", "ok"):
        await users_collection.update_one(
            {"user_id": target_id},
            {"$set": {"is_banned": False, "banned_reason": None}},
        )
        u = users.get(target_id)
        if u:
            u["is_banned"] = False
        await safe_send(
            context, target_id,
            _sidebar("Appeal Approved", "✅", [
                "✅ Your ban has been lifted!",
                "",
                "Welcome back — please follow the rules.",
            ]), parse_mode="HTML",
        )
        await update.message.reply_text(
            _sidebar("Approved", "✅", [f"✅ Unbanned <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
        _log_action(update.effective_user.id, f"approved appeal for {target_id}")

    elif action in ("reject", "deny", "no"):
        await safe_send(
            context, target_id,
            _sidebar("Appeal Rejected", "❌", [
                "❌ Your appeal was reviewed and denied.",
                "",
                "Contact owner for further queries.",
            ]), parse_mode="HTML",
        )
        await update.message.reply_text(
            _sidebar("Rejected", "❌",
                     [f"❌ Appeal rejected for <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
        _log_action(update.effective_user.id, f"rejected appeal for {target_id}")

    else:
        await update.message.reply_text("⚠️ Use 'approve' or 'reject'.")


# ══════════════════════════════════════════════════════════════
# CHECK CHANNEL
# ══════════════════════════════════════════════════════════════

async def cmd_checkchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")

    from services.community import _get_channel_chat_id, is_configured
    from config import COMMUNITY_CHANNEL_USERNAME, COMMUNITY_CHANNEL_ID

    if not is_configured():
        return await update.message.reply_text(
            "⚠️ Community channel not configured.\n"
            "Set COMMUNITY_CHANNEL_USERNAME or COMMUNITY_CHANNEL_ID in env.",
            parse_mode="HTML",
        )

    chat_id = _get_channel_chat_id()
    lines = [
        f"📢 <b>Channel Config</b>",
        f"  ├ Username : {COMMUNITY_CHANNEL_USERNAME or '—'}",
        f"  ├ ID : {COMMUNITY_CHANNEL_ID or '—'}",
        f"  └ Resolved : <code>{chat_id}</code>",
        "",
        "🔍 <b>Testing access...</b>",
    ]

    try:
        chat = await context.bot.get_chat(chat_id)
        lines.append(f"✅ Bot can access: <b>{chat.title}</b>")
        lines.append(f"  ├ Type : {chat.type}")
        lines.append(f"  └ ID : <code>{chat.id}</code>")

        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat_id, me.id)

        status = getattr(member, "status", "?")
        if hasattr(status, "value"):
            status = status.value
        lines.append("")
        lines.append("👤 <b>Bot Status in Channel</b>")
        lines.append(f"  ├ Status : <b>{status}</b>")

        if str(status).lower() in ("administrator", "creator"):
            lines.append("  └ ✅ <b>PERFECT — Bot is admin!</b>")
        else:
            lines.append("  └ ❌ <b>Bot is NOT admin — verify will fail!</b>")

    except Exception as e:
        lines.append(f"❌ <b>FAILED:</b> <code>{str(e)[:200]}</code>")
        lines.append("")
        lines.append("💡 <b>Fix:</b>")
        lines.append("  1. Channel → Manage → Administrators")
        lines.append("  2. Add Admin → search bot username")
        lines.append("  3. Add with default permissions")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# GIFTS
# ══════════════════════════════════════════════════════════════

async def cmd_gifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    from services.gifts import get_user_gifts, get_recent_gifts, count_user_gifts

    if context.args:
        target_id, doc = await resolve_user(context.args[0])
        if not target_id:
            return await _not_found(update, context.args[0])

        gifts = await get_user_gifts(target_id, limit=30)
        total = await count_user_gifts(target_id)

        if not gifts:
            return await update.message.reply_text(
                _sidebar("Gifts Received", "🎁", [
                    f"👤 User : <code>{target_id}</code>",
                    "",
                    "📭 No gifts received yet.",
                    "",
                    "💡 Gifts come from:",
                    "  ├ Referral milestones",
                    "  └ Bonus rewards",
                ]), parse_mode="HTML",
            )

        name = esc(doc.get("name") or "Unknown")
        uname = doc.get("username")
        uname_str = esc(f"@{uname}") if uname else "—"

        lines = [
            f"👤 User : <b>{name}</b>",
            f"🆔 ID : <code>{target_id}</code>",
            f"🔗 Username : {uname_str}",
            "",
            f"📊 Total gifts : <b>{total}</b>",
            f"📋 Showing : <b>{len(gifts)}</b> recent",
            "",
            "🎁 <b>Gift History</b>",
        ]

        for g in gifts[:20]:
            at = g.get("at")
            at_str = at.strftime("%d %b %Y %H:%M") if at else "—"
            gtype = esc(str(g.get("gift_type", "Gift")))
            gval = esc(str(g.get("gift_value", "?")))
            reason = esc(g.get("reason", "—"))
            lines.append(f"  • <b>+{gval} {gtype}</b>")
            lines.append(f"    └ {reason}")
            lines.append(f"    🕐 {at_str}")
            lines.append("")

        await update.message.reply_text(
            _sidebar("Gifts Received", "🎁", lines),
            parse_mode="HTML",
        )
        return

    gifts = await get_recent_gifts(limit=20)
    if not gifts:
        return await update.message.reply_text(
            _sidebar("Recent Gifts", "🎁", [
                "📭 No gifts sent yet.",
                "",
                "💡 Gifts are logged when:",
                "  ├ User hits referral milestone",
                "  └ Bonus events",
            ]), parse_mode="HTML",
        )

    lines = [
        f"Total recent : <b>{len(gifts)}</b>",
        "",
        "🎁 <b>Latest Gifts</b>",
    ]

    for g in gifts:
        uid_ = g.get("user_id")
        at = g.get("at")
        at_str = at.strftime("%d %b %H:%M") if at else "—"
        gtype = esc(str(g.get("gift_type", "Gift")))
        gval = esc(str(g.get("gift_value", "?")))
        lines.append(
            f"  • <code>{uid_}</code> +{gval} {gtype} ({at_str})"
        )

    lines.append("")
    lines.append("💡 Use /gifts <user_id> to see details")

    await update.message.reply_text(
        _sidebar("Recent Gifts", "🎁", lines),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# VIP INFO
# ══════════════════════════════════════════════════════════════

_PLAN_DAYS = {
    "Sprint": 14,
    "Gold": 30,
    "Diamond": 90,
    "Master": 180,
}


def _infer_plan_from_tier(tier_name: str) -> tuple:
    if not tier_name:
        return None, None
    t = str(tier_name)
    for key, days in _PLAN_DAYS.items():
        if key.lower() in t.lower():
            return key, days
    return None, None


def _infer_grant_date(expiry, days: int):
    if not expiry or not days:
        return None
    try:
        return expiry - timedelta(days=days)
    except Exception:
        return None


async def cmd_vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not context.args:
        return await update.message.reply_text(
            _sidebar("VIP Info", "👑", [
                "📌 <b>Usage:</b>",
                "<code>/vipinfo &lt;user_id|@username&gt;</code>",
                "",
                "💡 Shows:",
                "  ├ ⭐ Stars purchases",
                "  ├ 🛡️ Admin grants",
                "  ├ 🎁 Referral rewards",
                "  ├ ✨ Bonus rewards",
                "  ├ 📝 Backfilled records",
                "  └ 🕰️ Inferred legacy info",
            ]), parse_mode="HTML",
        )

    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    is_vip = bool(doc.get("is_vip", False))
    tier = doc.get("vip_tier_name") or "None"
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y") if exp else "—"

    plan_label, plan_days = _infer_plan_from_tier(tier)

    now = utcnow()
    days_left = None
    hours_left = None
    days_since_expiry = None
    hours_since_expiry = None
    if exp:
        try:
            seconds = (exp - now).total_seconds()
            if seconds >= 0:
                hours_left = int(seconds // 3600)
                days_left = int(seconds // 86400)
            else:
                seconds = abs(seconds)
                hours_since_expiry = int(seconds // 3600)
                days_since_expiry = int(seconds // 86400)
        except Exception:
            pass

    tier_clean = (tier or "").strip()
    has_tier = bool(tier_clean) and tier_clean.lower() != "none"

    if is_vip:
        display_tier = esc(tier_clean) if has_tier else "Unnamed VIP"
        status = f"👑 {display_tier}"
    else:
        if has_tier:
            status = f"⌛ Expired · was {esc(tier_clean)}"
        else:
            status = "⚪ Free Member"

    name = esc(doc.get("name") or "Unknown")
    uname = doc.get("username")
    uname_str = esc(f"@{uname}") if uname else "—"
    joined = doc.get("joined_date")
    joined_str = joined.strftime("%d %b %Y") if joined else "—"

    payment_history = doc.get("payment_history") or []
    gifts = []
    try:
        from services.gifts import get_user_gifts
        gifts = await get_user_gifts(target_id, limit=50)
    except Exception as e:
        logger.debug(f"gift lookup failed: {e}")

    stars_purchases = [h for h in payment_history if h.get("source") == "stars"]
    admin_grants = [h for h in payment_history if h.get("source") == "admin"]
    referral_rewards = [h for h in payment_history if h.get("source") == "referral"]
    bonus_entries = [h for h in payment_history if h.get("source") == "bonus"]
    backfilled = [h for h in payment_history if h.get("source") == "backfill"]
    legacy_entries = [h for h in payment_history if not h.get("source")]

    other_gifts = [
        g for g in gifts
        if "referral" not in str(g.get("reason", "")).lower()
        and "admin granted" not in str(g.get("reason", "")).lower()
        and "[backfill]" not in str(g.get("reason", "")).lower()
    ]

    total_events = (
        len(stars_purchases) + len(admin_grants) + len(referral_rewards)
        + len(bonus_entries) + len(backfilled) + len(legacy_entries)
    )

    is_legacy = (is_vip or has_tier) and total_events == 0

    lines = [
        f"👤 <b>{name}</b>",
        f"🆔 ID : <code>{target_id}</code>",
        f"🔗 Username : {uname_str}",
        f"📅 Joined : {joined_str}",
        "",
        "⭐  <b>Current Status</b>",
        f"  ├ Status : {status}",
        f"  ├ ⌛ Expires : {exp_str}",
    ]
    if days_left is not None:
        if days_left >= 1:
            lines.append(f"  ├ ⏰ Active for : <b>{days_left} days</b> more")
        elif hours_left is not None and hours_left >= 1:
            lines.append(f"  ├ ⏰ Active for : <b>~{hours_left} hours</b> more")
        else:
            lines.append(f"  ├ ⏰ Active for : <b>< 1 hour</b> (expiring soon!)")
    elif days_since_expiry is not None:
        if days_since_expiry >= 1:
            lines.append(f"  ├ ⏰ Expired : <b>{days_since_expiry} days ago</b>")
        else:
            lines.append(f"  ├ ⏰ Expired : <b>~{hours_since_expiry} hours ago</b>")
    lines.append(f"  └ 📊 Tracked events : <b>{total_events}</b>")

    if is_legacy:
        lines.append("")
        lines.append("🕰️  <b>Inferred Legacy VIP</b>")

        if has_tier:
            lines.append(f"  ├ Tier : {esc(tier_clean)}")
            if plan_label and plan_days:
                lines.append(f"  ├ Plan type : <b>{plan_label}</b>")
                lines.append(f"  ├ Duration : <b>{plan_days} days</b>")
                inferred = _infer_grant_date(exp, plan_days)
                if inferred:
                    lines.append(
                        f"  └ Est. grant : <b>{inferred.strftime('%d %b %Y')}</b>"
                    )
            else:
                lines.append("  └ <i>Unknown plan type</i>")
        else:
            lines.append("  └ <i>No tier info in DB</i>")
            lines.append("  └ <i>Grant method: unknown</i>")

        lines.append("")
        lines.append("⚠️  <b>Why no source?</b>")
        lines.append("  ├ VIP granted before tracking")
        lines.append("  ├ Old /addvip (pre-history)")
        lines.append("  ├ Old referral reward")
        lines.append("  └ Manual DB entry")
        lines.append("")
        lines.append("📝 <b>Backfill it:</b>")
        lines.append(
            f"  <code>/vipbackfill {target_id} {plan_days or 30} "
            f"\"Legacy grant\"</code>"
        )

    if stars_purchases:
        lines.append("")
        lines.append("⭐  <b>Stars Purchases</b>")
        for h in reversed(stars_purchases[-10:]):
            tier_name = esc(h.get("tier", "VIP"))
            days = h.get("days", "?")
            stars = h.get("stars", 0)
            at = str(h.get("at", ""))[:10]
            lines.append(f"  • {tier_name} (+{days}d) · {stars}⭐")
            lines.append(f"    └ 🕐 {at}")

    if admin_grants:
        lines.append("")
        lines.append("🛡️  <b>Admin Grants</b>")
        for h in reversed(admin_grants[-10:]):
            tier_name = esc(h.get("tier", "VIP"))
            days = h.get("days", "?")
            granted_by = h.get("granted_by", "?")
            at = str(h.get("at", ""))[:10]
            lines.append(f"  • {tier_name} (+{days}d)")
            lines.append(f"    └ 👤 by <code>{granted_by}</code> · 🕐 {at}")

    if referral_rewards:
        lines.append("")
        lines.append("🎁  <b>Referral Rewards</b>")
        for h in reversed(referral_rewards[-10:]):
            days = h.get("days", "?")
            note = esc(str(h.get("note", "—"))[:70])
            at = str(h.get("at", ""))[:10]
            lines.append(f"  • +{days}d VIP")
            lines.append(f"    └ {note}")
            lines.append(f"    └ 🕐 {at}")

    if bonus_entries:
        lines.append("")
        lines.append("✨  <b>Bonus Rewards</b>")
        for h in reversed(bonus_entries[-10:]):
            days = h.get("days", "?")
            note = esc(str(h.get("note", "—"))[:70])
            at = str(h.get("at", ""))[:10]
            lines.append(f"  • +{days}d VIP")
            lines.append(f"    └ {note}")
            lines.append(f"    └ 🕐 {at}")

    if backfilled:
        lines.append("")
        lines.append("📝  <b>Backfilled Records</b>")
        for h in reversed(backfilled[-10:]):
            days = h.get("days", "?")
            note = esc(str(h.get("note", "—"))[:60])
            at = str(h.get("at", ""))[:10]
            lines.append(f"  • +{days}d")
            lines.append(f"    └ 📌 {note}")
            lines.append(f"    └ 🕐 {at}")

    if legacy_entries:
        lines.append("")
        lines.append("📋  <b>Untagged Records</b>")
        lines.append("  <i>(old data — no source field)</i>")
        for h in reversed(legacy_entries[-5:]):
            tier_name = esc(h.get("tier", "VIP"))
            days = h.get("days", "?")
            stars = h.get("stars", 0)
            at = str(h.get("at", ""))[:10]
            tag = f" · {stars}⭐" if stars > 0 else ""
            lines.append(f"  • {tier_name} (+{days}d){tag} · {at}")

    if other_gifts:
        lines.append("")
        lines.append("🎁  <b>Other Gifts</b>")
        for g in other_gifts[:10]:
            gtype = esc(str(g.get("gift_type", "Gift")))
            val = g.get("gift_value", "?")
            reason = esc(str(g.get("reason", "—"))[:70])
            at = g.get("at")
            at_str = at.strftime("%d %b %Y") if at else "—"
            lines.append(f"  • +{val} {gtype}")
            lines.append(f"    └ {reason}")
            lines.append(f"    └ 🕐 {at_str}")

    if (not is_vip and not has_tier and total_events == 0):
        lines.append("")
        lines.append("📭 <i>No VIP history found.</i>")
        lines.append("💡 User has never received VIP.")

    await update.message.reply_text(
        _sidebar("VIP Source History", "👑", lines,
                 tip="Complete VIP record + legacy inference"),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# VIP BACKFILL
# ══════════════════════════════════════════════════════════════

async def cmd_vipbackfill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return

    if len(context.args) < 3:
        return await update.message.reply_text(
            _sidebar("VIP Backfill", "📝", [
                "📌 <b>Usage:</b>",
                "<code>/vipbackfill &lt;user_id&gt; &lt;days&gt; &lt;reason&gt;</code>",
                "",
                "📝 <b>Example:</b>",
                "<code>/vipbackfill 123456789 14 \"Old grant\"</code>",
            ]), parse_mode="HTML",
        )

    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    try:
        days = int(context.args[1])
        if days < 1 or days > 3650:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("⚠️ Days must be 1-3650.")

    reason = " ".join(context.args[2:])[:200]
    if not reason.strip():
        return await update.message.reply_text("⚠️ Reason required.")

    entry = {
        "plan": "BACKFILL",
        "tier": "📝 Backfilled VIP",
        "days": days,
        "price_inr": "—",
        "price_usd": "—",
        "stars": 0,
        "at": utcnow().isoformat(),
        "source": "backfill",
        "granted_by": update.effective_user.id,
        "note": reason,
    }

    try:
        await users_collection.update_one(
            {"user_id": target_id},
            {"$push": {"payment_history": entry}},
        )
    except Exception as e:
        logger.error(f"VIP backfill DB write failed: {e}")
        return await update.message.reply_text(
            _sidebar("Failed", "❌", [f"DB write error: <code>{e}</code>"]),
            parse_mode="HTML",
        )

    u_cached = users.get(target_id)
    if u_cached:
        ph = u_cached.get("payment_history") or []
        ph.append(entry)
        u_cached["payment_history"] = ph[-20:]

    _log_action(
        update.effective_user.id,
        f"backfilled VIP {days}d for {target_id}: {reason[:50]}"
    )

    await update.message.reply_text(
        _sidebar("Backfilled", "✅", [
            f"✅ VIP record added",
            f"🎯 User : <code>{target_id}</code>",
            f"📦 Days : +{days}",
            f"📝 Reason : {esc(reason)}",
            "",
            f"💡 Use <code>/vipinfo {target_id}</code> to verify",
        ]), parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# INVITES
# ══════════════════════════════════════════════════════════════

async def cmd_invites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if users_collection is None:
        return await update.message.reply_text("⚠️ DB not connected.")

    if context.args:
        target_id, doc = await resolve_user(context.args[0])
        if not target_id:
            return await _not_found(update, context.args[0])

        total = await users_collection.count_documents(
            {"referred_by": target_id}
        )

        rewards_earned = 0
        try:
            from services.gifts import get_user_gifts
            gifts = await get_user_gifts(target_id, limit=100)
            rewards_earned = len([
                g for g in gifts
                if "referral" in str(g.get("reason", "")).lower()
            ])
        except Exception:
            pass

        from config import REFERRAL_REWARDS_NEEDED

        name = esc(doc.get("name") or "Unknown")
        uname = doc.get("username")
        uname_str = esc(f"@{uname}") if uname else "—"

        in_batch = total % REFERRAL_REWARDS_NEEDED
        remaining = (
            REFERRAL_REWARDS_NEEDED - in_batch
            if in_batch else REFERRAL_REWARDS_NEEDED
        )

        lines = [
            f"👤 User : <b>{name}</b>",
            f"🆔 ID : <code>{target_id}</code>",
            f"🔗 Username : {uname_str}",
            "",
            "👥  <b>Referral Stats</b>",
            f"  ├ Total invites : <b>{total}</b>",
            f"  ├ Rewards earned : <b>{rewards_earned} VIP days</b>",
            f"  ├ Progress : <b>{in_batch}/{REFERRAL_REWARDS_NEEDED}</b>",
            f"  └ Next reward in : <b>{remaining}</b> more",
        ]

        if total > 0:
            lines.append("")
            lines.append("📋  <b>Referred Users</b>")
            count = 0
            async for ref_doc in users_collection.find(
                {"referred_by": target_id},
                {"user_id": 1, "name": 1, "username": 1, "joined_date": 1},
            ).sort("joined_date", -1).limit(20):
                count += 1
                r_name = esc(ref_doc.get("name") or "—")
                r_uid = ref_doc.get("user_id")
                r_jd = ref_doc.get("joined_date")
                r_jd_str = r_jd.strftime("%d %b") if r_jd else "—"
                lines.append(
                    f"  {count}. <code>{r_uid}</code> — {r_name} ({r_jd_str})"
                )

            if total > 20:
                lines.append(f"  <i>…and {total - 20} more</i>")

        await update.message.reply_text(
            _sidebar("Referral Stats", "👥", lines),
            parse_mode="HTML",
        )
        return

    pipeline = [
        {"$match": {"referred_by": {"$ne": None}}},
        {"$group": {"_id": "$referred_by", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]

    try:
        results = await users_collection.aggregate(pipeline).to_list(20)
    except Exception as e:
        logger.error(f"invites aggregate failed: {e}")
        return await update.message.reply_text("⚠️ Query failed.")

    if not results:
        return await update.message.reply_text(
            _sidebar("Leaderboard", "🏆", [
                "📭 No referrals yet.",
                "",
                "💡 Users can invite via /invite",
            ]), parse_mode="HTML",
        )

    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = [f"Top referrers : <b>{len(results)}</b>", ""]
    total_invites_all = 0

    for i, row in enumerate(results):
        uid_ = row["_id"]
        count = row["count"]
        total_invites_all += count
        medal = medals.get(i, f"{i+1}.")

        info = await users_collection.find_one(
            {"user_id": uid_}, {"name": 1}
        )
        name = esc((info or {}).get("name") or "—")

        lines.append(f"  {medal} <code>{uid_}</code> — {name}")
        lines.append(f"     └ <b>{count}</b> invites")

    lines.append("")
    lines.append(f"📊 Total invites (top 20) : <b>{total_invites_all}</b>")
    lines.append("💡 Use /invites &lt;user_id&gt; for details")

    await update.message.reply_text(
        _sidebar("Top Referrers", "🏆", lines),
        parse_mode="HTML",
    )