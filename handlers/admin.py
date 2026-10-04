import html
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import users, queue, queue_set, queue_lock, admin_cache
from database import is_owner_or_admin, users_collection, safe_count
from utils import safe_send
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


async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"], "Admin only command."),
            parse_mode="HTML",
        )
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️",
                     ["Usage:", "<code>/addvip &lt;user_id&gt; &lt;plan_key&gt;</code>"],
                     "Example: /addvip 123456 PLAN_1M"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
        plan_key = context.args[1].upper()
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid plan key."]), parse_mode="HTML"
        )
    if users_collection is None:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Database offline."]), parse_mode="HTML"
        )
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [f"User <code>{target}</code> not found."]),
            parse_mode="HTML",
        )
    new_exp = await activate_vip(target, plan_key)
    plan = VIP_PLANS[plan_key]

    user_text = _sidebar(
        "VIP Activated", "🎉",
        [
            "🎉 Your VIP is now active!",
            "",
            "👑 <b>Your Plan</b>",
            f"  ├ 🌟 {plan['name']}",
            f"  └ ⌛ Until : {new_exp.strftime('%d %b %Y')}",
        ],
        "Enjoy your VIP perks!",
    )
    await safe_send(context, target, user_text, parse_mode="HTML")

    admin_text = _sidebar(
        "Success", "✅",
        [
            f"✅ Granted <b>{plan['name']}</b>",
            "",
            "📋 <b>Details</b>",
            f"  ├ 🎯 User : <code>{target}</code>",
            f"  └ 📦 Plan : {plan['label']}",
        ],
    )
    await update.message.reply_text(admin_text, parse_mode="HTML")
    logger.info(f"ADMIN {update.effective_user.id} granted VIP {plan_key} to {target}")


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/removevip &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [f"User <code>{target}</code> not found."]),
            parse_mode="HTML",
        )
    await users_collection.update_one(
        {"user_id": target},
        {"$set": {"is_vip": False, "vip_expiry_date": None,
                  "vip_tier_name": "None", "pref_gender": "Any"}},
    )
    u = users.get(target)
    if u:
        u["is_vip"] = False
        u["vip_expiry_date"] = None
        u["vip_tier_name"] = "None"
        u["pref_gender"] = "Any"

    await safe_send(
        context, target,
        _sidebar("VIP Removed", "⌛", ["⌛ Your VIP has been removed."]),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ VIP removed from <code>{target}</code>."]),
        parse_mode="HTML",
    )


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/ban &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [f"User <code>{target}</code> not found."]),
            parse_mode="HTML",
        )
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": True}})
    admin_cache.discard(target)

    u = users.get(target)
    if u:
        u["is_banned"] = True
        if u.get("partner"):
            await disconnect(context, target, u["partner"], ender_id=target)
        async with queue_lock:
            try:
                queue.remove(target)
            except ValueError:
                pass
            queue_set.discard(target)
        u["state"] = "IDLE"

    await safe_send(
        context, target,
        _sidebar("Banned", "🚫", ["🚫 You have been banned."]),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ User <code>{target}</code> banned."]),
        parse_mode="HTML",
    )
    logger.info(f"ADMIN {update.effective_user.id} banned {target}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/unban &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [f"User <code>{target}</code> not found."]),
            parse_mode="HTML",
        )
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": False}})
    u = users.get(target)
    if u:
        u["is_banned"] = False

    doc = await users_collection.find_one({"user_id": target}, {"is_admin": 1})
    if doc and doc.get("is_admin"):
        admin_cache.add(target)

    await safe_send(
        context, target,
        _sidebar("Unbanned", "✅", ["✅ You have been unbanned. Welcome back!"]),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ User <code>{target}</code> unbanned."]),
        parse_mode="HTML",
    )


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/userinfo &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if users_collection is None:
        return
    doc = await users_collection.find_one({"user_id": target})
    if not doc:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [f"User <code>{target}</code> not found."]),
            parse_mode="HTML",
        )
    mem = users.get(target, {})
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y") if exp else "—"

    lines = [
        "🆔  <b>Identity</b>",
        f"  ├ 🆔 <code>{target}</code>",
        f"  ├ 👤 {doc.get('name') or '—'} | @{doc.get('username') or '—'}",
        f"  ├ 🚻 {doc.get('gender') or '—'} | 🎂 {doc.get('age') or '—'}",
        f"  ├ 🌍 {doc.get('country') or '—'}",
        f"  ├ 📝 {html.escape(doc.get('bio') or '—')}",
        f"  └ 🏷️ {', '.join(doc.get('interests') or []) or 'None'}",
        "",
        "⭐  <b>Status</b>",
        f"  ├ ⭐ VIP : {'Yes' if doc.get('is_vip') else 'No'} ({doc.get('vip_tier_name', 'None')})",
        f"  ├ ⌛ Expiry : {exp_str}",
        f"  ├ 🚫 Banned : {'Yes' if doc.get('is_banned') else 'No'}",
        f"  └ 🛡️ Admin : {'Yes' if doc.get('is_admin') else 'No'}",
        "",
        "⚡  <b>Runtime</b>",
        f"  ├ ⚡ State : {mem.get('state', 'offline')}",
        f"  └ 🤝 Partner : {mem.get('partner') or 'None'}",
    ]
    await update.message.reply_text(
        _sidebar("User Info", "👤", lines), parse_mode="HTML"
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    total = await safe_count()
    vip = await safe_count({"is_vip": True})
    banned = await safe_count({"is_banned": True})
    admins = await safe_count({"is_admin": True})
    active = sum(1 for u in users.values() if u.get("state") == "CHAT") // 2
    searching = sum(1 for u in users.values() if u.get("state") == "SEARCHING")

    lines = [
        "👥  <b>Users</b>",
        f"  ├ Total : <b>{total}</b>",
        f"  ├ ⭐ VIP : <b>{vip}</b>",
        f"  ├ 🚫 Banned : <b>{banned}</b>",
        f"  └ 🛡️ Admins : <b>{admins}</b>",
        "",
        "🟢  <b>Live</b>",
        f"  ├ Online : <b>{len(users)}</b>",
        f"  ├ 💬 Chats : <b>{active}</b>",
        f"  ├ 🔍 Searching : <b>{searching}</b>",
        f"  └ 📋 Queue : <b>{len(queue)}</b>",
    ]
    await update.message.reply_text(
        _sidebar("Stats", "📊", lines), parse_mode="HTML"
    )


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )

    # ✅ Get raw message text (preserves line breaks & HTML)
    raw_text = update.message.text or ""
    # Remove "/broadcast" prefix
    parts = raw_text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage:",
                "<code>/broadcast &lt;message&gt;</code>",
                "",
                "Send as multi-line message for best results.",
            ], "Tip: Compose message, then add /broadcast at start."),
            parse_mode="HTML",
        )

    message = parts[1].strip()
    context.user_data["pending_broadcast"] = message

    preview_text = _sidebar(
        "Confirm Broadcast", "📢",
        [
            "📢 <b>Preview</b>",
            "",
            message,
            "",
            "⚠️ Will be sent to ALL users (excluding banned).",
        ],
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send to All", callback_data="BC_CONFIRM"),
        InlineKeyboardButton("❌ Cancel", callback_data="BC_CANCEL"),
    ]])
    await update.message.reply_text(preview_text, reply_markup=kb, parse_mode="HTML")


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/dm &lt;user_id&gt; &lt;message&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    message = " ".join(context.args[1:])

    text = _sidebar("Message from Admin", "📩", [message, "", "— SparkTalks Team"])
    result = await safe_send(context, target, text, parse_mode="HTML")
    if result:
        await update.message.reply_text(
            _sidebar("Sent", "✅", [f"✅ Delivered to <code>{target}</code>."]),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            _sidebar("Failed", "❌", [f"❌ Could not deliver to <code>{target}</code>."]),
            parse_mode="HTML",
        )
    logger.info(f"ADMIN {update.effective_user.id} → DM {target}: {message[:80]!r}")


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/forceend &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    u = users.get(target)
    if not u:
        return await update.message.reply_text(
            _sidebar("Not Online", "📴", [f"User <code>{target}</code> is offline."]),
            parse_mode="HTML",
        )
    if u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(target)
            except ValueError:
                pass
            queue_set.discard(target)
        u["state"] = "IDLE"
        await safe_send(
            context, target,
            _sidebar("Ended", "🛑", ["🛑 Search ended by admin."]),
            parse_mode="HTML",
        )
        await update.message.reply_text(
            _sidebar("Done", "✅", [f"✅ Search cancelled for <code>{target}</code>."]),
            parse_mode="HTML",
        )
        return
    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text(
            _sidebar("Idle", "💤", [f"User <code>{target}</code> is not in a chat."]),
            parse_mode="HTML",
        )
    await disconnect(context, target, partner, ender_id=target)
    await update.message.reply_text(
        _sidebar("Done", "✅",
                 [f"✅ Chat between <code>{target}</code> & <code>{partner}</code> ended."]),
        parse_mode="HTML",
    )


async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"]), parse_mode="HTML"
        )
    lines = []
    if users_collection is not None:
        async for doc in users_collection.find(
            {"is_banned": True},
            {"user_id": 1, "name": 1, "username": 1},
        ).limit(50):
            name = doc.get("name") or "—"
            uname = f"@{doc['username']}" if doc.get("username") else "—"
            lines.append(f"• <code>{doc['user_id']}</code> {name} ({uname})")

    if not lines:
        lines = ["No banned users."]

    await update.message.reply_text(
        _sidebar("Ban List", "🚫", lines), parse_mode="HTML"
    )


async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Only Owner can promote admins."]),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/setadmin &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if target == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Owner is already super-admin."]), parse_mode="HTML"
        )
    if users_collection is None:
        return
    await users_collection.update_one(
        {"user_id": target},
        {"$set": {"is_admin": True, "user_id": target}},
        upsert=True,
    )
    u = users.get(target)
    if u:
        u["is_admin"] = True
    admin_cache.add(target)

    await safe_send(
        context, target,
        _sidebar("Admin Granted", "🛡️", ["🛡️ You are now an Admin."]),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ <code>{target}</code> is now Admin."]),
        parse_mode="HTML",
    )


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Access Denied", "🔒", ["⛔ Only Owner can remove admins."]),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/removeadmin &lt;user_id&gt;</code>"]),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid user ID."]), parse_mode="HTML"
        )
    if target == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Cannot remove Owner."]), parse_mode="HTML"
        )
    if users_collection is None:
        return
    await users_collection.update_one({"user_id": target}, {"$set": {"is_admin": False}})
    u = users.get(target)
    if u:
        u["is_admin"] = False
    admin_cache.discard(target)

    await safe_send(
        context, target,
        _sidebar("Admin Removed", "🛡️", ["🛡️ Your admin access has been revoked."]),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Admin removed from <code>{target}</code>."]),
        parse_mode="HTML",
    )