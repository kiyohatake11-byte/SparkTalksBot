import asyncio
import html
from telegram import Update
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import users, queue, queue_lock, admin_cache
from database import is_owner_or_admin, users_collection
from utils import spark_card, safe_send
from services.vip import activate_vip
from services.matching import disconnect


async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if len(context.args) < 2:
        return await update.message.reply_text(
            spark_card("Error", "⚠️ Usage:\n<code>/addvip &lt;user_id&gt; &lt;plan_key&gt;</code>"), parse_mode="HTML"
        )
    try:
        target = int(context.args[0])
        plan_key = context.args[1].upper()
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid plan key."), parse_mode="HTML")
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(spark_card("Not Found", f"⚠️ User <code>{target}</code> not found."), parse_mode="HTML")
    new_exp = await activate_vip(target, plan_key)
    plan = VIP_PLANS[plan_key]
    await safe_send(context, target, spark_card("VIP Activated", f"🎉 <b>{plan['name']}</b>\n⌛ Until: {new_exp.strftime('%d %b %Y')}"), parse_mode="HTML")
    await update.message.reply_text(spark_card("Success", f"✅ Granted <b>{plan['name']}</b> to <code>{target}</code>."), parse_mode="HTML")


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/removevip &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(spark_card("Not Found", f"⚠️ User <code>{target}</code> not found."), parse_mode="HTML")
    await users_collection.update_one(
        {"user_id": target},
        {"$set": {"is_vip": False, "vip_expiry_date": None, "vip_tier_name": "None", "pref_gender": "Any"}}
    )
    u = users.get(target)
    if u:
        u["is_vip"] = False
        u["vip_expiry_date"] = None
        u["vip_tier_name"] = "None"
        u["pref_gender"] = "Any"
    await safe_send(context, target, spark_card("VIP Removed", "⌛ Your VIP has been removed."), parse_mode="HTML")
    await update.message.reply_text(spark_card("Success", f"✅ VIP removed from <code>{target}</code>."), parse_mode="HTML")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/ban &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(spark_card("Not Found", f"⚠️ User <code>{target}</code> not found."), parse_mode="HTML")
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": True}})
    u = users.get(target)
    if u:
        u["is_banned"] = True
        if u.get("partner"):
            await disconnect(context, target, u["partner"])
        async with queue_lock:
            try:
                queue.remove(target)
            except ValueError:
                pass
        u["state"] = "IDLE"
    await safe_send(context, target, spark_card("Banned", "🚫 You have been banned."), parse_mode="HTML")
    await update.message.reply_text(spark_card("Success", f"✅ User <code>{target}</code> banned."), parse_mode="HTML")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/unban &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(spark_card("Not Found", f"⚠️ User <code>{target}</code> not found."), parse_mode="HTML")
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": False}})
    u = users.get(target)
    if u:
        u["is_banned"] = False
    await safe_send(context, target, spark_card("Unbanned", "✅ You have been unbanned. Welcome back!"), parse_mode="HTML")
    await update.message.reply_text(spark_card("Success", f"✅ User <code>{target}</code> unbanned."), parse_mode="HTML")


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/userinfo &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    doc = await users_collection.find_one({"user_id": target})
    if not doc:
        return await update.message.reply_text(spark_card("Not Found", f"⚠️ User <code>{target}</code> not found."), parse_mode="HTML")
    mem = users.get(target, {})
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y %H:%M") if exp else "—"
    body = (
        f"🆔 <code>{target}</code>\n"
        f"👤 {doc.get('name') or '—'}  |  @{doc.get('username') or '—'}\n"
        f"🚻 {doc.get('gender') or '—'}  |  🎂 {doc.get('age') or '—'}  |  🌍 {doc.get('country') or '—'}\n"
        f"📝 {html.escape(doc.get('bio') or '—')}\n"
        f"🏷️ {', '.join(doc.get('interests') or []) or 'None'}\n\n"
        f"⭐ VIP: {'Yes' if doc.get('is_vip') else 'No'} ({doc.get('vip_tier_name', 'None')})\n"
        f"⌛ Expiry: {exp_str}\n"
        f"🚫 Banned: {'Yes' if doc.get('is_banned') else 'No'}\n"
        f"🛡️ Admin: {'Yes' if doc.get('is_admin') else 'No'}\n\n"
        f"⚡ State: {mem.get('state', 'offline')}\n"
        f"🤝 Partner: {mem.get('partner') or 'None'}"
    )
    await update.message.reply_text(spark_card("User Info", body, "Admin"), parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    total = await users_collection.count_documents({})
    vip = await users_collection.count_documents({"is_vip": True})
    banned = await users_collection.count_documents({"is_banned": True})
    admins = await users_collection.count_documents({"is_admin": True})
    active = sum(1 for u in users.values() if u.get("state") == "CHAT") // 2
    searching = sum(1 for u in users.values() if u.get("state") == "SEARCHING")
    body = (
        f"📊 <b>SparkTalks Stats</b>\n\n"
        f"👥 Total: <b>{total}</b>\n"
        f"⭐ VIP: <b>{vip}</b>\n"
        f"🚫 Banned: <b>{banned}</b>\n"
        f"🛡️ Admins: <b>{admins}</b>\n\n"
        f"🟢 Online: <b>{len(users)}</b>\n"
        f"💬 Chats: <b>{active}</b>\n"
        f"🔍 Searching: <b>{searching}</b>\n"
        f"📋 Queue: <b>{len(queue)}</b>"
    )
    await update.message.reply_text(spark_card("Stats", body, "Admin"), parse_mode="HTML")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/broadcast &lt;message&gt;</code>"), parse_mode="HTML")
    message = " ".join(context.args)
    text = spark_card("Announcement", message, "SparkTalks Team")
    sent = failed = 0
    async for doc in users_collection.find({}, {"user_id": 1}):
        if await safe_send(context, doc["user_id"], text, parse_mode="HTML"):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(spark_card("Broadcast Done", f"✅ Sent: <b>{sent}</b>\n❌ Failed: <b>{failed}</b>"), parse_mode="HTML")


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if len(context.args) < 2:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/dm &lt;user_id&gt; &lt;message&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    message = " ".join(context.args[1:])
    text = spark_card("Message from Admin", message, "SparkTalks")
    result = await safe_send(context, target, text, parse_mode="HTML")
    if result:
        await update.message.reply_text(spark_card("Sent", f"✅ Delivered to <code>{target}</code>."), parse_mode="HTML")
    else:
        await update.message.reply_text(spark_card("Failed", f"❌ Could not deliver to <code>{target}</code>."), parse_mode="HTML")


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/forceend &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    u = users.get(target)
    if not u:
        return await update.message.reply_text(spark_card("Not Online", f"⚠️ User <code>{target}</code> is offline."), parse_mode="HTML")
    if u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(target)
            except ValueError:
                pass
        u["state"] = "IDLE"
        await safe_send(context, target, spark_card("Ended", "🛑 Search ended by admin."), parse_mode="HTML")
        await update.message.reply_text(spark_card("Done", f"✅ Search cancelled for <code>{target}</code>."), parse_mode="HTML")
        return
    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text(spark_card("Idle", f"⚠️ User <code>{target}</code> is not in a chat."), parse_mode="HTML")
    await disconnect(context, target, partner)
    await update.message.reply_text(spark_card("Done", f"✅ Chat between <code>{target}</code> & <code>{partner}</code> ended."), parse_mode="HTML")


async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Unauthorized!"), parse_mode="HTML")
    lines = []
    async for doc in users_collection.find({"is_banned": True}, {"user_id": 1, "name": 1, "username": 1}).limit(50):
        name = doc.get("name") or "—"
        uname = f"@{doc['username']}" if doc.get("username") else "—"
        lines.append(f"• <code>{doc['user_id']}</code>  {name} ({uname})")
    body = "No banned users." if not lines else "🚫 <b>Banned</b> (max 50)\n\n" + "\n".join(lines)
    await update.message.reply_text(spark_card("Ban List", body, "Admin"), parse_mode="HTML")


async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Only Owner can promote admins."), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/setadmin &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    if target == OWNER_ID:
        return await update.message.reply_text(spark_card("Error", "⚠️ Owner is already super-admin."), parse_mode="HTML")
    await users_collection.update_one({"user_id": target}, {"$set": {"is_admin": True, "user_id": target}}, upsert=True)
    u = users.get(target)
    if u:
        u["is_admin"] = True
    admin_cache.add(target)
    await safe_send(context, target, spark_card("Admin Granted", "🛡️ You are now an Admin."), parse_mode="HTML")
    await update.message.reply_text(spark_card("Success", f"✅ <code>{target}</code> is now Admin."), parse_mode="HTML")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(spark_card("Access Denied", "⛔ Only Owner can remove admins."), parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(spark_card("Error", "⚠️ Usage: <code>/removeadmin &lt;user_id&gt;</code>"), parse_mode="HTML")
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(spark_card("Error", "⚠️ Invalid user ID."), parse_mode="HTML")
    if target == OWNER_ID:
        return await update.message.reply_text(spark_card("Error", "⚠️ Cannot remove Owner."), parse_mode="HTML")
    await users_collection.update_one({"user_id": target}, {"$set": {"is_admin": False}})
    u = users.get(target)
    if u:
        u["is_admin"] = False
    admin_cache.discard(target)
    await safe_send(context, target, spark_card("Admin Removed", "🛡️ Your admin access has been revoked."), parse_mode="HTML")
    await update.message.reply_text(spark_card("Success", f"✅ Admin removed from <code>{target}</code>."), parse_mode="HTML")
