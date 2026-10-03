import html
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import users, queue, queue_set, queue_lock, admin_cache
from database import is_owner_or_admin, users_collection
from utils import box_card, box_simple, safe_send, to_bold
from services.vip import activate_vip
from services.matching import disconnect

logger = logging.getLogger("sparktalks")


async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if len(context.args) < 2:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage:\n<code>/addvip &lt;user_id&gt; &lt;plan_key&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
        plan_key = context.args[1].upper()
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid plan key.", emoji="⚠️"), parse_mode="HTML"
        )
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"⚠️ User <code>{target}</code> not found.", emoji="🔍"),
            parse_mode="HTML",
        )
    new_exp = await activate_vip(target, plan_key)
    plan = VIP_PLANS[plan_key]

    user_body = box_card(
        "VIP Activated",
        [
            {"type": "text", "content": "🎉 Your VIP is now active!"},
            {"type": "divider"},
            {"type": "section", "emoji": "👑", "heading": "Your Plan"},
            {"type": "line", "content": f"🌟 {plan['name']}"},
            {"type": "line", "content": f"⌛ Until: {new_exp.strftime('%d %b %Y')}"},
        ],
        emoji="🎉",
    )
    await safe_send(context, target, user_body, parse_mode="HTML")

    admin_body = box_card(
        "Success",
        [
            {"type": "text", "content": f"✅ Granted <b>{plan['name']}</b>"},
            {"type": "divider"},
            {"type": "kv", "items": [
                (f"🎯 {to_bold('User')}", f"<code>{target}</code>"),
                (f"📦 {to_bold('Plan')}", plan["label"]),
            ]},
        ],
        emoji="✅",
    )
    await update.message.reply_text(admin_body, parse_mode="HTML")
    logger.info(f"ADMIN {update.effective_user.id} granted VIP {plan_key} to {target}")


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/removevip &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"⚠️ User <code>{target}</code> not found.", emoji="🔍"),
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

    await safe_send(context, target,
                    box_simple("VIP Removed", "⌛ Your VIP has been removed.", emoji="⌛"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"✅ VIP removed from <code>{target}</code>.", emoji="✅"),
        parse_mode="HTML",
    )


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/ban &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"⚠️ User <code>{target}</code> not found.", emoji="🔍"),
            parse_mode="HTML",
        )
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": True}})

    # ✅ Remove from admin_cache if applicable
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

    await safe_send(context, target,
                    box_simple("Banned", "🚫 You have been banned.", emoji="🚫"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"✅ User <code>{target}</code> banned.", emoji="✅"),
        parse_mode="HTML",
    )
    logger.info(f"ADMIN {update.effective_user.id} banned {target}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/unban &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"⚠️ User <code>{target}</code> not found.", emoji="🔍"),
            parse_mode="HTML",
        )
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": False}})
    u = users.get(target)
    if u:
        u["is_banned"] = False

    # ✅ Re-add to admin cache if user is still admin
    doc = await users_collection.find_one({"user_id": target}, {"is_admin": 1})
    if doc and doc.get("is_admin"):
        admin_cache.add(target)

    await safe_send(context, target,
                    box_simple("Unbanned", "✅ You have been unbanned. Welcome back!", emoji="✅"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"✅ User <code>{target}</code> unbanned.", emoji="✅"),
        parse_mode="HTML",
    )


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/userinfo &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    doc = await users_collection.find_one({"user_id": target})
    if not doc:
        return await update.message.reply_text(
            box_simple("Not Found", f"⚠️ User <code>{target}</code> not found.", emoji="🔍"),
            parse_mode="HTML",
        )
    mem = users.get(target, {})
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y %H:%M") if exp else "—"

    body = box_card(
        "User Info",
        [
            {"type": "section", "emoji": "🆔", "heading": "Identity"},
            {"type": "line", "content": f"🆔 <code>{target}</code>"},
            {"type": "line", "content": f"👤 {doc.get('name') or '—'} | @{doc.get('username') or '—'}"},
            {"type": "line", "content": f"🚻 {doc.get('gender') or '—'} | 🎂 {doc.get('age') or '—'}"},
            {"type": "line", "content": f"🌍 {doc.get('country') or '—'}"},
            {"type": "line", "content": f"📝 {html.escape(doc.get('bio') or '—')}"},
            {"type": "line", "content": f"🏷️ {', '.join(doc.get('interests') or []) or 'None'}"},
            {"type": "divider"},
            {"type": "section", "emoji": "⭐", "heading": "Status"},
            {"type": "line", "content": f"⭐ VIP: {'Yes' if doc.get('is_vip') else 'No'} ({doc.get('vip_tier_name', 'None')})"},
            {"type": "line", "content": f"⌛ Expiry: {exp_str}"},
            {"type": "line", "content": f"🚫 Banned: {'Yes' if doc.get('is_banned') else 'No'}"},
            {"type": "line", "content": f"🛡️ Admin: {'Yes' if doc.get('is_admin') else 'No'}"},
            {"type": "divider"},
            {"type": "section", "emoji": "⚡", "heading": "Runtime"},
            {"type": "line", "content": f"⚡ State: {mem.get('state', 'offline')}"},
            {"type": "line", "content": f"🤝 Partner: {mem.get('partner') or 'None'}"},
        ],
        emoji="👤",
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    total = await users_collection.count_documents({})
    vip = await users_collection.count_documents({"is_vip": True})
    banned = await users_collection.count_documents({"is_banned": True})
    admins = await users_collection.count_documents({"is_admin": True})
    active = sum(1 for u in users.values() if u.get("state") == "CHAT") // 2
    searching = sum(1 for u in users.values() if u.get("state") == "SEARCHING")

    body = box_card(
        "Stats",
        [
            {"type": "section", "emoji": "👥", "heading": "Users"},
            {"type": "line", "content": f"👥 Total: <b>{total}</b>"},
            {"type": "line", "content": f"⭐ VIP: <b>{vip}</b>"},
            {"type": "line", "content": f"🚫 Banned: <b>{banned}</b>"},
            {"type": "line", "content": f"🛡️ Admins: <b>{admins}</b>"},
            {"type": "divider"},
            {"type": "section", "emoji": "🟢", "heading": "Live"},
            {"type": "line", "content": f"🟢 Online: <b>{len(users)}</b>"},
            {"type": "line", "content": f"💬 Chats: <b>{active}</b>"},
            {"type": "line", "content": f"🔍 Searching: <b>{searching}</b>"},
            {"type": "line", "content": f"📋 Queue: <b>{len(queue)}</b>"},
        ],
        emoji="📊",
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview + confirm before broadcasting."""
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/broadcast &lt;message&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    message = " ".join(context.args)
    context.user_data["pending_broadcast"] = message

    preview = box_card(
        "Confirm Broadcast",
        [
            {"type": "section", "emoji": "📢", "heading": "Message Preview"},
            {"type": "text", "content": message},
            {"type": "divider"},
            {"type": "text", "content": "⚠️ This will be sent to ALL users."},
        ],
        emoji="📢",
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send to All", callback_data="BC_CONFIRM"),
        InlineKeyboardButton("❌ Cancel", callback_data="BC_CANCEL"),
    ]])
    await update.message.reply_text(preview, reply_markup=kb, parse_mode="HTML")


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if len(context.args) < 2:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/dm &lt;user_id&gt; &lt;message&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    message = " ".join(context.args[1:])

    text = box_card(
        "Message from Admin",
        [
            {"type": "text", "content": message},
            {"type": "divider"},
            {"type": "text", "content": "— SparkTalks Team"},
        ],
        emoji="📩",
    )
    result = await safe_send(context, target, text, parse_mode="HTML")
    if result:
        await update.message.reply_text(
            box_simple("Sent", f"✅ Delivered to <code>{target}</code>.", emoji="✅"),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            box_simple("Failed", f"❌ Could not deliver to <code>{target}</code>.", emoji="❌"),
            parse_mode="HTML",
        )
    # ✅ Audit log
    logger.info(
        f"ADMIN {update.effective_user.id} → DM {target}: {message[:80]!r}"
    )


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/forceend &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    u = users.get(target)
    if not u:
        return await update.message.reply_text(
            box_simple("Not Online", f"⚠️ User <code>{target}</code> is offline.", emoji="📴"),
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
        await safe_send(context, target,
                        box_simple("Ended", "🛑 Search ended by admin.", emoji="🛑"),
                        parse_mode="HTML")
        await update.message.reply_text(
            box_simple("Done", f"✅ Search cancelled for <code>{target}</code>.", emoji="✅"),
            parse_mode="HTML",
        )
        return
    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text(
            box_simple("Idle", f"⚠️ User <code>{target}</code> is not in a chat.", emoji="💤"),
            parse_mode="HTML",
        )
    await disconnect(context, target, partner, ender_id=target)
    await update.message.reply_text(
        box_simple("Done",
                   f"✅ Chat between <code>{target}</code> & <code>{partner}</code> ended.",
                   emoji="✅"),
        parse_mode="HTML",
    )


async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Unauthorized!", emoji="🔒"), parse_mode="HTML"
        )
    lines = []
    async for doc in users_collection.find(
        {"is_banned": True},
        {"user_id": 1, "name": 1, "username": 1},
    ).limit(50):
        name = doc.get("name") or "—"
        uname = f"@{doc['username']}" if doc.get("username") else "—"
        lines.append(f"• <code>{doc['user_id']}</code> {name} ({uname})")

    body_text = "No banned users." if not lines else "\n".join(lines)
    body = box_card("Ban List", [{"type": "text", "content": body_text}], emoji="🚫")
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Only Owner can promote admins.", emoji="🔒"),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/setadmin &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    if target == OWNER_ID:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Owner is already super-admin.", emoji="⚠️"),
            parse_mode="HTML",
        )
    await users_collection.update_one(
        {"user_id": target},
        {"$set": {"is_admin": True, "user_id": target}},
        upsert=True,
    )
    u = users.get(target)
    if u:
        u["is_admin"] = True
    admin_cache.add(target)

    await safe_send(context, target,
                    box_simple("Admin Granted", "🛡️ You are now an Admin.", emoji="🛡️"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"✅ <code>{target}</code> is now Admin.", emoji="✅"),
        parse_mode="HTML",
    )


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            box_simple("Access Denied", "⛔ Only Owner can remove admins.", emoji="🔒"),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Usage: <code>/removeadmin &lt;user_id&gt;</code>", emoji="⚠️"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Invalid user ID.", emoji="⚠️"), parse_mode="HTML"
        )
    if target == OWNER_ID:
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Cannot remove Owner.", emoji="⚠️"), parse_mode="HTML"
        )
    await users_collection.update_one({"user_id": target}, {"$set": {"is_admin": False}})
    u = users.get(target)
    if u:
        u["is_admin"] = False
    admin_cache.discard(target)

    await safe_send(context, target,
                    box_simple("Admin Removed", "🛡️ Your admin access has been revoked.", emoji="🛡️"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"✅ Admin removed from <code>{target}</code>.", emoji="✅"),
        parse_mode="HTML",
    )