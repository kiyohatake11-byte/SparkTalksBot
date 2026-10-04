import html
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import (
    users, queue, queue_set, queue_lock, admin_cache,
    muted_users, admin_logs,
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
        parts.append("▎")
        parts.append(f"💡 <i>{tip}</i>")
    return "\n".join(parts)


def _log_action(admin_id: int, action: str):
    admin_logs.append({
        "time": utcnow().strftime("%H:%M"),
        "admin": admin_id,
        "action": action,
    })


async def _deny(update):
    return await update.message.reply_text(
        _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"], "Admin only command."),
        parse_mode="HTML",
    )


async def _not_found(update, identifier):
    return await update.message.reply_text(
        _sidebar("Not Found", "🔍", [f"User <code>{identifier}</code> not found."]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# 👤 USER MANAGEMENT
# ══════════════════════════════════════════════════════════════

async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage:",
                "<code>/addvip &lt;user_id|@username&gt; &lt;plan&gt;</code>",
                "",
                "Plans: PLAN_14D, PLAN_1M, PLAN_3M, PLAN_6M",
                "Example: /addvip @rahul PLAN_1M",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    plan_key = context.args[1].upper()
    if not target_id:
        return await _not_found(update, context.args[0])
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid plan key."]), parse_mode="HTML"
        )
    new_exp = await activate_vip(target_id, plan_key)
    plan = VIP_PLANS[plan_key]

    await safe_send(context, target_id, _sidebar(
        "VIP Activated", "🎉",
        ["🎉 Your VIP is now active!", "",
         "👑 <b>Your Plan</b>",
         f"  ├ 🌟 {plan['name']}",
         f"  └ ⌛ Until : {new_exp.strftime('%d %b %Y')}"],
        "Enjoy your VIP perks!"
    ), parse_mode="HTML")

    await update.message.reply_text(_sidebar(
        "Success", "✅",
        [f"✅ Granted <b>{plan['name']}</b>", "",
         "📋 <b>Details</b>",
         f"  ├ 🎯 User : <code>{target_id}</code>",
         f"  └ 📦 Plan : {plan['label']}"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"granted VIP {plan_key} to {target_id}")


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/removevip &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_vip": False, "vip_expiry_date": None,
                  "vip_tier_name": "None", "pref_gender": "Any"}},
    )
    u = users.get(target_id)
    if u:
        u["is_vip"] = False
        u["vip_expiry_date"] = None
        u["vip_tier_name"] = "None"
        u["pref_gender"] = "Any"

    await safe_send(context, target_id,
                    _sidebar("VIP Removed", "⌛", ["⌛ Your VIP has been removed."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ VIP removed from <code>{target_id}</code>."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"removed VIP from {target_id}")


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 1:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/warn &lt;id|@user&gt; [reason]</code>",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason given"

    # Ensure warnings is a list
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []

    warnings.append({
        "reason": reason,
        "by": update.effective_user.id,
        "at": utcnow().isoformat(),
    })

    # 3 warnings = auto-ban
    if len(warnings) >= 3:
        await users_collection.update_one(
            {"user_id": target_id},
            {"$set": {
                "warnings": warnings,
                "is_banned": True,
                "banned_reason": "Auto-ban: 3 warnings",
                "banned_at": utcnow(),
                "banned_by": update.effective_user.id,
            }},
        )
        u = users.get(target_id)
        if u:
            u["warnings"] = warnings
            u["is_banned"] = True

        await safe_send(context, target_id,
            _sidebar("Auto-Banned", "🚫", [
                "🚫 You have been banned!",
                "",
                "📋 <b>Reason</b>",
                "  └ 3 warnings reached",
            ]), parse_mode="HTML")

        await update.message.reply_text(_sidebar(
            "Auto-Banned", "🚫",
            [f"⚠️ 3rd warning → User auto-banned",
             "",
             "📋 <b>Details</b>",
             f"  ├ 🎯 User : <code>{target_id}</code>",
             f"  ├ 📝 Reason : {reason}",
             f"  └ ⚠️ Warnings : 3/3"],
        ), parse_mode="HTML")
        _log_action(update.effective_user.id, f"auto-banned {target_id} (3 warnings)")
        return

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"warnings": warnings}},
    )
    u = users.get(target_id)
    if u:
        u["warnings"] = warnings

    await safe_send(context, target_id,
        _sidebar("Warning", "⚠️", [
            f"⚠️ You received a warning!",
            "",
            "📋 <b>Details</b>",
            f"  ├ 📝 Reason : {reason}",
            f"  ├ ⚠️ Total : {len(warnings)}/3",
            f"  └ ⏰ {utcnow().strftime('%d %b %Y %H:%M')}",
            "",
            "⚠️ 3 warnings = automatic ban!",
        ]), parse_mode="HTML")

    await update.message.reply_text(_sidebar(
        "User Warned", "⚠️",
        [f"⚠️ Warning #{len(warnings)} issued",
         "",
         "📋 <b>Details</b>",
         f"  ├ 🎯 User : <code>{target_id}</code>",
         f"  ├ 📝 Reason : {reason}",
         f"  └ ⚠️ Warnings : {len(warnings)}/3"],
        f"{3 - len(warnings)} more warnings = auto-ban",
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"warned {target_id}: {reason}")


async def cmd_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/warnings &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []

    if not warnings:
        return await update.message.reply_text(_sidebar(
            "Warnings", "✅",
            [f"👤 User : <code>{target_id}</code>",
             f"📊 Total : <b>0</b>",
             "",
             "✅ No warnings. User is clean!"],
        ), parse_mode="HTML")

    lines = [
        f"👤 User : <code>{target_id}</code>",
        f"📊 Total : <b>{len(warnings)}/3</b>",
        "",
        "⚠️ <b>Warning History</b>",
    ]
    for i, w in enumerate(warnings):
        prefix = "└" if i == len(warnings) - 1 else "├"
        reason = w.get("reason", "—")
        at = w.get("at", "")[:10] if w.get("at") else "—"
        lines.append(f"  {prefix} #{i+1} — {reason} ({at})")

    await update.message.reply_text(
        _sidebar("Warnings", "⚠️", lines), parse_mode="HTML"
    )


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/unwarn &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list) or not warnings:
        return await update.message.reply_text(_sidebar(
            "Nothing to Unwarn", "ℹ️",
            [f"User <code>{target_id}</code> has no warnings."],
        ), parse_mode="HTML")

    warnings.pop()  # Remove last warning
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"warnings": warnings}},
    )
    u = users.get(target_id)
    if u:
        u["warnings"] = warnings

    await update.message.reply_text(_sidebar(
        "Warning Removed", "✅",
        [f"✅ Last warning removed",
         "",
         "📋 <b>Details</b>",
         f"  ├ 🎯 User : <code>{target_id}</code>",
         f"  └ ⚠️ Remaining : {len(warnings)}/3"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"removed warning from {target_id}")


async def cmd_resetprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/resetprofile &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {
            "gender": None, "age": None, "country": None,
            "bio": None, "interests": [],
            "pref_gender": "Any", "profile_public": False,
            "confirm_media": True,
        }},
    )
    u = users.get(target_id)
    if u:
        for key in ["gender", "age", "country", "bio"]:
            u[key] = None
        u["interests"] = []
        u["pref_gender"] = "Any"
        u["profile_public"] = False
        u["confirm_media"] = True
        u["state"] = "IDLE"
        u["partner"] = None

    await safe_send(context, target_id,
        _sidebar("Profile Reset", "🔄",
                 ["🔄 Your profile has been reset by admin.",
                  "",
                  "💡 Please run /start to set up again."]),
        parse_mode="HTML")
    await update.message.reply_text(_sidebar(
        "Success", "✅",
        [f"✅ Profile reset for <code>{target_id}</code>."],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"reset profile for {target_id}")


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/resetstats &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"total_chats": 0, "total_matches": 0, "report_count": 0}},
    )
    u = users.get(target_id)
    if u:
        u["total_chats"] = 0
        u["total_matches"] = 0
        u["report_count"] = 0

    await update.message.reply_text(_sidebar(
        "Success", "✅",
        [f"✅ Stats reset for <code>{target_id}</code>"],
         "",
         "📊 <b>Reset Values</b>",
         "  ├ 💬 Chats : 0",
         "  ├ 🏆 Matches : 0",
         "  └ 🚨 Reports : 0",
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"reset stats for {target_id}")


async def cmd_whois(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/whois &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    mem = users.get(target_id, {})
    name = doc.get("name") or "—"
    uname = f"@{doc.get('username')}" if doc.get("username") else "—"

    if doc.get("is_vip"):
        status = f"👑 {doc.get('vip_tier_name', 'VIP')}"
    else:
        status = "⚪ Free Member"

    state_str = mem.get("state", "offline")
    if mem.get("partner"):
        state_str = "🟢 In Chat"
    elif state_str == "SEARCHING":
        state_str = "🟡 Searching"
    else:
        state_str = "⚪ Idle"

    warnings = doc.get("warnings", [])
    w_count = len(warnings) if isinstance(warnings, list) else 0

    await update.message.reply_text(_sidebar(
        "Quick Lookup", "🔍",
        [f"👤 <b>{name}</b> ({uname})",
         f"🆔 <code>{target_id}</code>",
         "",
         "⚡ <b>Status</b>",
         f"  ├ {status}",
         f"  ├ 💬 Chats : {doc.get('total_chats', 0)}",
         f"  ├ 🏆 Matches : {doc.get('total_matches', 0)}",
         f"  ├ ⚠️ Warnings : {w_count}/3",
         f"  ├ 🚫 Banned : {'Yes' if doc.get('is_banned') else 'No'}",
         f"  └ 🟢 Now : {state_str}"],
    ), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# 🚫 MODERATION
# ══════════════════════════════════════════════════════════════

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/ban &lt;id|@user&gt; [reason]</code>",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason given"

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {
            "is_banned": True,
            "banned_reason": reason,
            "banned_at": utcnow(),
            "banned_by": update.effective_user.id,
        }},
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
        _sidebar("Banned", "🚫", [
            "🚫 You have been banned!",
            "",
            "📋 <b>Details</b>",
            f"  └ 📝 Reason : {reason}",
        ]), parse_mode="HTML")

    await update.message.reply_text(_sidebar(
        "Success", "✅",
        [f"✅ User banned",
         "",
         "📋 <b>Details</b>",
         f"  ├ 🎯 User : <code>{target_id}</code>",
         f"  └ 📝 Reason : {reason}"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"banned {target_id}: {reason}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/unban &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
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
        _sidebar("Unbanned", "✅", ["✅ You have been unbanned. Welcome back!"]),
        parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ User <code>{target_id}</code> unbanned."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"unbanned {target_id}")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/kick &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    u = users.get(target_id)
    if not u:
        return await update.message.reply_text(
            _sidebar("Offline", "📴", [f"User <code>{target_id}</code> is offline."]),
            parse_mode="HTML",
        )

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
        _sidebar("Kicked", "👢", [
            "👢 You have been kicked by admin.",
            "",
            "💡 You can continue using the bot.",
        ]), parse_mode="HTML")

    await update.message.reply_text(_sidebar(
        "Success", "✅",
        [f"✅ User kicked from {kicked_from}",
         "",
         "📋 <b>Details</b>",
         f"  └ 🎯 User : <code>{target_id}</code>"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"kicked {target_id} from {kicked_from}")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/mute &lt;id|@user&gt; [minutes]</code>",
                "Default: 10 minutes",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    try:
        minutes = int(context.args[1]) if len(context.args) > 1 else 10
    except ValueError:
        minutes = 10

    unmute_at = utcnow() + timedelta(minutes=minutes)
    muted_users[target_id] = unmute_at.timestamp()

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"muted_until": unmute_at}},
    )

    await safe_send(context, target_id,
        _sidebar("Muted", "🔇", [
            f"🔇 You have been muted!",
            "",
            "📋 <b>Details</b>",
            f"  ├ ⏱ Duration : {minutes} min",
            f"  └ ⏰ Until : {unmute_at.strftime('%H:%M')} UTC",
        ]), parse_mode="HTML")

    await update.message.reply_text(_sidebar(
        "Success", "✅",
        [f"✅ User muted for {minutes} min",
         "",
         "📋 <b>Details</b>",
         f"  ├ 🎯 User : <code>{target_id}</code>",
         f"  └ ⏰ Until : {unmute_at.strftime('%H:%M')} UTC"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"muted {target_id} for {minutes}m")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/unmute &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    muted_users.pop(target_id, None)
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"muted_until": None}},
    )

    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ User <code>{target_id}</code> unmuted."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"unmuted {target_id}")


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/forceend &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    u = users.get(target_id)
    if not u:
        return await update.message.reply_text(
            _sidebar("Not Online", "📴", [f"User <code>{target_id}</code> offline."]),
            parse_mode="HTML",
        )

    if u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
        await safe_send(context, target_id,
                        _sidebar("Ended", "🛑", ["🛑 Search ended by admin."]),
                        parse_mode="HTML")
        await update.message.reply_text(
            _sidebar("Done", "✅", [f"✅ Search cancelled for <code>{target_id}</code>."]),
            parse_mode="HTML",
        )
        return

    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text(
            _sidebar("Idle", "💤", [f"User <code>{target_id}</code> not in a chat."]),
            parse_mode="HTML",
        )
    await disconnect(context, target_id, partner, ender_id=target_id)
    await update.message.reply_text(
        _sidebar("Done", "✅",
                 [f"✅ Chat between <code>{target_id}</code> & <code>{partner}</code> ended."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"force-ended chat for {target_id}")


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
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/blocked &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    blocked_ids = doc.get("blocked_users", []) or []
    name = doc.get("name") or "Unknown"
    uname = f"@{doc.get('username')}" if doc.get("username") else "—"

    if not blocked_ids:
        return await update.message.reply_text(_sidebar(
            "Blocked Users", "🚫",
            [f"👤 User : <b>{name}</b> ({uname})",
             f"🆔 ID : <code>{target_id}</code>",
             "",
             "✅ No users blocked."],
        ), parse_mode="HTML")

    lines = [
        f"👤 User : <b>{name}</b> ({uname})",
        f"🆔 ID : <code>{target_id}</code>",
        f"📊 Total blocked : <b>{len(blocked_ids)}</b>",
        "",
        "🚫 <b>Blocked List</b>",
    ]
    cursor = users_collection.find(
        {"user_id": {"$in": blocked_ids}},
        {"user_id": 1, "name": 1, "username": 1, "is_banned": 1},
    )
    blocked_info = {}
    async for b in cursor:
        blocked_info[b["user_id"]] = b

    for i, bid in enumerate(blocked_ids):
        prefix = "└" if i == len(blocked_ids) - 1 else "├"
        info = blocked_info.get(bid)
        if info:
            bname = info.get("name") or "Unknown"
            buname = f"@{info.get('username')}" if info.get("username") else "—"
            banned_flag = " 🚫" if info.get("is_banned") else ""
            lines.append(f"  {prefix} <code>{bid}</code> — {bname} ({buname}){banned_flag}")
        else:
            lines.append(f"  {prefix} <code>{bid}</code> — Unknown")

    await update.message.reply_text(
        _sidebar("Blocked Users", "🚫", lines), parse_mode="HTML"
    )


async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage:",
                "<code>/unblock &lt;id|@user&gt; &lt;blocked_id|@user&gt;</code>",
                "",
                "Example: /unblock @rahul @amit",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    blocked_id, _ = await resolve_user(context.args[1])
    if not blocked_id:
        return await _not_found(update, context.args[1])

    blocked_list = doc.get("blocked_users", []) or []
    if blocked_id not in blocked_list:
        return await update.message.reply_text(_sidebar(
            "Not Blocked", "⚠️",
            [f"User <code>{target_id}</code> ne",
             f"<code>{blocked_id}</code> ko block nahi kiya."],
        ), parse_mode="HTML")

    new_list = [bid for bid in blocked_list if bid != blocked_id]
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"blocked_users": new_list}}
    )
    u = users.get(target_id)
    if u:
        u["blocked_users"] = new_list

    await update.message.reply_text(_sidebar(
        "Unblocked", "🔓",
        ["✅ Removed from block list!",
         "",
         "📋 <b>Details</b>",
         f"  ├ 👤 User : <code>{target_id}</code>",
         f"  ├ 🎯 Unblocked : <code>{blocked_id}</code>",
         f"  └ 📊 Remaining : {len(new_list)}"],
        "Both users can now match again!",
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"unblocked {blocked_id} from {target_id}")


async def cmd_clearblocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/clearblocks &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    count = len(doc.get("blocked_users", []) or [])
    if count == 0:
        return await update.message.reply_text(_sidebar(
            "Nothing to Clear", "ℹ️",
            [f"User <code>{target_id}</code> has no blocks."],
        ), parse_mode="HTML")

    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"blocked_users": []}}
    )
    u = users.get(target_id)
    if u:
        u["blocked_users"] = []

    await update.message.reply_text(_sidebar(
        "All Cleared", "🔓",
        [f"✅ Cleared <b>{count}</b> blocked users!",
         "",
         "📋 <b>Details</b>",
         f"  └ 👤 User : <code>{target_id}</code>"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"cleared {count} blocks for {target_id}")


# ══════════════════════════════════════════════════════════════
# 📊 ANALYTICS
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
         f"  └ 📋 Queue : <b>{len(queue)}</b>"],
    ), parse_mode="HTML")


async def cmd_topusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    cursor = users_collection.find(
        {"is_banned": False}, {"user_id": 1, "name": 1, "total_chats": 1}
    ).sort("total_chats", -1).limit(10)
    rank = 1
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    async for doc in cursor:
        medal = medals.get(rank, f"{rank}.")
        name = doc.get("name") or "—"
        chats = doc.get("total_chats", 0)
        lines.append(f"  {medal} {name} — {chats} chats")
        rank += 1

    if not lines:
        lines = ["No data available."]

    await update.message.reply_text(
        _sidebar("Top Users", "🏆", ["📊 <b>By Total Chats</b>"] + lines),
        parse_mode="HTML",
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    cursor = users_collection.find(
        {"is_banned": False}, {"user_id": 1, "name": 1, "joined_date": 1}
    ).sort("joined_date", -1).limit(10)
    async for doc in cursor:
        name = doc.get("name") or "—"
        jd = doc.get("joined_date")
        jd_str = jd.strftime("%d %b") if jd else "—"
        lines.append(f"  • <code>{doc['user_id']}</code> {name} ({jd_str})")

    if not lines:
        lines = ["No recent users."]

    await update.message.reply_text(
        _sidebar("Recent Users", "🆕", ["📋 <b>Last 10 joined</b>"] + lines),
        parse_mode="HTML",
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
        lines.append(f"  • <code>{doc['user_id']}</code> {name}")
        lines.append(f"     └ 👑 {tier} · ⌛ {exp_str}")
        count += 1

    if not lines:
        lines = ["✅ No VIP users yet."]

    await update.message.reply_text(
        _sidebar(f"VIP Users ({count})", "👑", lines),
        parse_mode="HTML",
    )


async def cmd_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = [f"📋 Queue size : <b>{len(queue)}</b>", ""]
    if queue:
        lines.append("🔍 <b>Waiting Users</b>")
        for i, uid in enumerate(list(queue)[:10]):
            u = users.get(uid)
            name = u.get("name") if u else "Unknown"
            prefix = "└" if i == min(9, len(queue) - 1) else "├"
            lines.append(f"  {prefix} <code>{uid}</code> — {name}")

    await update.message.reply_text(
        _sidebar("Queue Status", "🔍", lines), parse_mode="HTML"
    )


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    chats = []
    seen = set()
    for uid, u in users.items():
        if u.get("state") == "CHAT" and u.get("partner") and uid not in seen:
            pid = u["partner"]
            seen.add(uid)
            seen.add(pid)
            u1 = users.get(uid, {})
            u2 = users.get(pid, {})
            n1 = u1.get("name") or "?"
            n2 = u2.get("name") or "?"
            chats.append(f"  • <code>{uid}</code> ({n1}) ↔ <code>{pid}</code> ({n2})")

    if not chats:
        chats = ["✅ No active chats."]

    await update.message.reply_text(
        _sidebar(f"Active Chats ({len(chats)})", "💬", chats),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# 📢 COMMUNICATION
# ══════════════════════════════════════════════════════════════

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    raw_text = update.message.text or ""
    parts = raw_text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/broadcast &lt;message&gt;</code>",
                "",
                "Multi-line message supported.",
            ]), parse_mode="HTML",
        )

    message = parts[1].strip()
    context.user_data["pending_broadcast"] = message

    preview_text = _sidebar(
        "Confirm Broadcast", "📢",
        ["📢 <b>Preview</b>", "", message, "",
         "⚠️ Will be sent to ALL users (excluding banned)."],
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send to All", callback_data="BC_CONFIRM"),
        InlineKeyboardButton("❌ Cancel", callback_data="BC_CANCEL"),
    ]])
    await update.message.reply_text(preview_text, reply_markup=kb, parse_mode="HTML")


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/dm &lt;id|@user&gt; &lt;message&gt;</code>",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    message = " ".join(context.args[1:])
    text = _sidebar("Message from Admin", "📩",
                    [message, "", "— SparkTalks Team"])
    result = await safe_send(context, target_id, text, parse_mode="HTML")

    if result:
        await update.message.reply_text(
            _sidebar("Sent", "✅", [f"✅ Delivered to <code>{target_id}</code>."]),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            _sidebar("Failed", "❌", [f"❌ Could not deliver to <code>{target_id}</code>."]),
            parse_mode="HTML",
        )
    _log_action(update.effective_user.id, f"DM to {target_id}: {message[:50]}")


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/notify &lt;id|@user&gt; &lt;message&gt;</code>",
            ]), parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    message = " ".join(context.args[1:])
    result = await safe_send(context, target_id,
                             f"🔔 <i>{message}</i>", parse_mode="HTML")

    if result:
        await update.message.reply_text(
            _sidebar("Notified", "🔔", [f"✅ Silent notify sent to <code>{target_id}</code>."]),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            _sidebar("Failed", "❌", [f"❌ Could not notify <code>{target_id}</code>."]),
            parse_mode="HTML",
        )


# ══════════════════════════════════════════════════════════════
# 🛡️ ADMIN MANAGEMENT
# ══════════════════════════════════════════════════════════════

async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Only Owner can promote."]),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/setadmin &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    if target_id == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Owner is already admin."]), parse_mode="HTML"
        )

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_admin": True}},
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
    _log_action(update.effective_user.id, f"promoted {target_id} to admin")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Only Owner can demote."]),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/removeadmin &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    if target_id == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Cannot remove Owner."]), parse_mode="HTML"
        )

    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"is_admin": False}}
    )
    admin_cache.discard(target_id)
    u = users.get(target_id)
    if u:
        u["is_admin"] = False

    await safe_send(context, target_id,
        _sidebar("Admin Removed", "🛡️", ["🛡️ Your admin access has been revoked."]),
        parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Admin removed from <code>{target_id}</code>."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"demoted {target_id}")


async def cmd_adminlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = [f"👑 <b>Owner</b>", f"  └ <code>{OWNER_ID}</code>"]
    if len(admin_cache) > 1:
        lines.append("")
        lines.append("🛡️ <b>Admins</b>")
        others = [aid for aid in admin_cache if aid != OWNER_ID]
        for i, aid in enumerate(others):
            prefix = "└" if i == len(others) - 1 else "├"
            u = users.get(aid)
            name = u.get("name") if u else "—"
            lines.append(f"  {prefix} <code>{aid}</code> — {name}")

    await update.message.reply_text(
        _sidebar("Admin List", "🛡️", lines), parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════
# ⚙️ SYSTEM
# ══════════════════════════════════════════════════════════════

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    import psutil if False else None  # placeholder
    try:
        import psutil
        proc = psutil.Process()
        mem_mb = proc.memory_info().rss / 1024 / 1024
        mem_str = f"{mem_mb:.1f} MB"
    except Exception:
        mem_str = "N/A"

    db_ok = "✅ OK" if users_collection is not None else "❌ Down"
    maint = "🚧 ON" if state.maintenance_mode else "✅ OFF"

    await update.message.reply_text(_sidebar(
        "System Health", "💚",
        ["🖥️ <b>Bot</b>",
         f"  ├ Memory : {mem_str}",
         f"  └ Status : ✅ Healthy",
         "",
         "🗄️ <b>Database</b>",
         f"  ├ Connection : {db_ok}",
         f"  └ Users cached : {len(users)}",
         "",
         "📋 <b>Runtime</b>",
         f"  ├ Queue : {len(queue)}",
         f"  ├ Admins : {len(admin_cache)}",
         f"  ├ Muted : {len(muted_users)}",
         f"  └ Maintenance : {maint}"],
    ), parse_mode="HTML")


async def cmd_clearchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/clearchat &lt;id|@user&gt;</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    u = users.get(target_id)
    if not u:
        return await update.message.reply_text(
            _sidebar("Not Online", "📴", [f"User <code>{target_id}</code> offline."]),
            parse_mode="HTML",
        )

    old_state = u.get("state", "IDLE")
    old_partner = u.get("partner")

    if old_partner:
        pu = users.get(old_partner)
        if pu:
            pu["partner"] = None
            pu["state"] = "IDLE"

    u["partner"] = None
    u["state"] = "IDLE"
    u["pending_media"] = {}
    async with queue_lock:
        try:
            queue.remove(target_id)
        except ValueError:
            pass
        queue_set.discard(target_id)

    await update.message.reply_text(_sidebar(
        "State Cleared", "🧹",
        ["✅ User state reset to IDLE",
         "",
         "📋 <b>Details</b>",
         f"  ├ 👤 User : <code>{target_id}</code>",
         f"  ├ 🎯 Old state : {old_state}",
         f"  └ 🟢 New state : IDLE"],
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, f"cleared chat for {target_id}")


async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Only Owner can toggle."]),
            parse_mode="HTML",
        )
    if not context.args:
        status = "🚧 ON" if state.maintenance_mode else "✅ OFF"
        return await update.message.reply_text(
            _sidebar("Maintenance", "🚧", [
                f"Current status : {status}",
                "",
                "Usage: <code>/maintenance on</code> or <code>off</code>",
            ]), parse_mode="HTML"
        )

    arg = context.args[0].lower()
    if arg in ("on", "true", "1"):
        state.maintenance_mode = True
        await update.message.reply_text(_sidebar(
            "Maintenance ON", "🚧",
            ["🚧 Maintenance mode activated",
             "",
             "ℹ️ <b>What happens</b>",
             "  ├ 🔒 New matches paused",
             "  ├ 💬 Existing chats continue",
             "  └ 🛡️ Admins still active",
             "",
             "💡 /maintenance off to resume"],
        ), parse_mode="HTML")
        _log_action(update.effective_user.id, "enabled maintenance")
    elif arg in ("off", "false", "0"):
        state.maintenance_mode = False
        await update.message.reply_text(_sidebar(
            "Maintenance OFF", "✅",
            ["✅ Maintenance mode disabled",
             "",
             "💡 Bot is fully operational."],
        ), parse_mode="HTML")
        _log_action(update.effective_user.id, "disabled maintenance")
    else:
        await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Use <code>/maintenance on</code> or <code>off</code>"]),
            parse_mode="HTML"
        )


async def cmd_clearcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    from state import last_next_time, message_reactions_map
    cleared = len(users)
    users.clear()
    last_next_time.clear()
    message_reactions_map.clear()
    muted_users.clear()

    await update.message.reply_text(_sidebar(
        "Cache Cleared", "🧹",
        ["✅ In-memory cache cleared!",
         "",
         "📋 <b>Cleared</b>",
         f"  ├ Users : {cleared}",
         "  ├ Cooldowns",
         "  ├ Reaction maps",
         "  └ Muted users"],
        "Users will reload from DB on next message",
    ), parse_mode="HTML")
    _log_action(update.effective_user.id, "cleared cache")


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
        return await update.message.reply_text(
            _sidebar("Logs", "📝", ["No actions logged yet."]),
            parse_mode="HTML"
        )

    lines = [f"📊 Last {min(count, len(admin_logs))} actions", ""]
    for entry in list(admin_logs)[-count:]:
        lines.append(f"  [{entry['time']}] <code>{entry['admin']}</code>")
        lines.append(f"    └ {entry['action']}")

    await update.message.reply_text(
        _sidebar("Admin Logs", "📝", lines), parse_mode="HTML"
    )