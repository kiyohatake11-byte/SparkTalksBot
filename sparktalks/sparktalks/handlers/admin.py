import html
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import users, queue, queue_set, queue_lock, admin_cache
from database import is_owner_or_admin, users_collection, safe_count
from utils import box_card, box_simple, safe_send, to_bold
from services.vip import activate_vip
from services.matching import disconnect

logger = logging.getLogger("sparktalks")


async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if len(context.args) < 2:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage:\n<code>/addvip &lt;user_id&gt; &lt;plan_key&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
        plan_key = context.args[1].upper()
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid plan key.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if users_collection is None:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Database offline.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"\u26A0\uFE0F User <code>{target}</code> not found.", emoji="\U0001F50D"),
            parse_mode="HTML",
        )
    new_exp = await activate_vip(target, plan_key)
    plan = VIP_PLANS[plan_key]

    user_body = box_card(
        "VIP Activated",
        [
            {"type": "text", "content": "\U0001F389 Your VIP is now active!"},
            {"type": "divider"},
            {"type": "section", "emoji": "\U0001F451", "heading": "Your Plan"},
            {"type": "line", "content": f"\U0001F31F {plan['name']}"},
            {"type": "line", "content": f"\u231B Until: {new_exp.strftime('%d %b %Y')}"},
        ],
        emoji="\U0001F389",
    )
    await safe_send(context, target, user_body, parse_mode="HTML")

    admin_body = box_card(
        "Success",
        [
            {"type": "text", "content": f"\u2705 Granted <b>{plan['name']}</b>"},
            {"type": "divider"},
            {"type": "kv", "items": [
                (f"\U0001F3AF {to_bold('User')}", f"<code>{target}</code>"),
                (f"\U0001F4E6 {to_bold('Plan')}", plan["label"]),
            ]},
        ],
        emoji="\u2705",
    )
    await update.message.reply_text(admin_body, parse_mode="HTML")
    logger.info(f"ADMIN {update.effective_user.id} granted VIP {plan_key} to {target}")


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/removevip &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"\u26A0\uFE0F User <code>{target}</code> not found.", emoji="\U0001F50D"),
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
                    box_simple("VIP Removed", "\u231B Your VIP has been removed.", emoji="\u231B"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"\u2705 VIP removed from <code>{target}</code>.", emoji="\u2705"),
        parse_mode="HTML",
    )


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/ban &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"\u26A0\uFE0F User <code>{target}</code> not found.", emoji="\U0001F50D"),
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

    await safe_send(context, target,
                    box_simple("Banned", "\U0001F6AB You have been banned.", emoji="\U0001F6AB"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"\u2705 User <code>{target}</code> banned.", emoji="\u2705"),
        parse_mode="HTML",
    )
    logger.info(f"ADMIN {update.effective_user.id} banned {target}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/unban &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": target}, {"_id": 1})
    if not existing:
        return await update.message.reply_text(
            box_simple("Not Found", f"\u26A0\uFE0F User <code>{target}</code> not found.", emoji="\U0001F50D"),
            parse_mode="HTML",
        )
    await users_collection.update_one({"user_id": target}, {"$set": {"is_banned": False}})
    u = users.get(target)
    if u:
        u["is_banned"] = False

    doc = await users_collection.find_one({"user_id": target}, {"is_admin": 1})
    if doc and doc.get("is_admin"):
        admin_cache.add(target)

    await safe_send(context, target,
                    box_simple("Unbanned", "\u2705 You have been unbanned. Welcome back!", emoji="\u2705"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"\u2705 User <code>{target}</code> unbanned.", emoji="\u2705"),
        parse_mode="HTML",
    )


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/userinfo &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if users_collection is None:
        return
    doc = await users_collection.find_one({"user_id": target})
    if not doc:
        return await update.message.reply_text(
            box_simple("Not Found", f"\u26A0\uFE0F User <code>{target}</code> not found.", emoji="\U0001F50D"),
            parse_mode="HTML",
        )
    mem = users.get(target, {})
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y %H:%M") if exp else "-"

    body = box_card(
        "User Info",
        [
            {"type": "section", "emoji": "\U0001F194", "heading": "Identity"},
            {"type": "line", "content": f"\U0001F194 <code>{target}</code>"},
            {"type": "line", "content": f"\U0001F464 {doc.get('name') or '-'} | @{doc.get('username') or '-'}"},
            {"type": "line", "content": f"\U0001F6BB {doc.get('gender') or '-'} | \U0001F382 {doc.get('age') or '-'}"},
            {"type": "line", "content": f"\U0001F30D {doc.get('country') or '-'}"},
            {"type": "line", "content": f"\U0001F4DD {html.escape(doc.get('bio') or '-')}"},
            {"type": "line", "content": f"\U0001F3F7\uFE0F {', '.join(doc.get('interests') or []) or 'None'}"},
            {"type": "divider"},
            {"type": "section", "emoji": "\u2B50", "heading": "Status"},
            {"type": "line", "content": f"\u2B50 VIP: {'Yes' if doc.get('is_vip') else 'No'} ({doc.get('vip_tier_name', 'None')})"},
            {"type": "line", "content": f"\u231B Expiry: {exp_str}"},
            {"type": "line", "content": f"\U0001F6AB Banned: {'Yes' if doc.get('is_banned') else 'No'}"},
            {"type": "line", "content": f"\U0001F6E1\uFE0F Admin: {'Yes' if doc.get('is_admin') else 'No'}"},
            {"type": "divider"},
            {"type": "section", "emoji": "\u26A1", "heading": "Runtime"},
            {"type": "line", "content": f"\u26A1 State: {mem.get('state', 'offline')}"},
            {"type": "line", "content": f"\U0001F91D Partner: {mem.get('partner') or 'None'}"},
        ],
        emoji="\U0001F464",
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    total = await safe_count()
    vip = await safe_count({"is_vip": True})
    banned = await safe_count({"is_banned": True})
    admins = await safe_count({"is_admin": True})
    active = sum(1 for u in users.values() if u.get("state") == "CHAT") // 2
    searching = sum(1 for u in users.values() if u.get("state") == "SEARCHING")

    body = box_card(
        "Stats",
        [
            {"type": "section", "emoji": "\U0001F465", "heading": "Users"},
            {"type": "line", "content": f"\U0001F465 Total: <b>{total}</b>"},
            {"type": "line", "content": f"\u2B50 VIP: <b>{vip}</b>"},
            {"type": "line", "content": f"\U0001F6AB Banned: <b>{banned}</b>"},
            {"type": "line", "content": f"\U0001F6E1\uFE0F Admins: <b>{admins}</b>"},
            {"type": "divider"},
            {"type": "section", "emoji": "\U0001F7E2", "heading": "Live"},
            {"type": "line", "content": f"\U0001F7E2 Online: <b>{len(users)}</b>"},
            {"type": "line", "content": f"\U0001F4AC Chats: <b>{active}</b>"},
            {"type": "line", "content": f"\U0001F50D Searching: <b>{searching}</b>"},
            {"type": "line", "content": f"\U0001F4CB Queue: <b>{len(queue)}</b>"},
        ],
        emoji="\U0001F4CA",
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/broadcast &lt;message&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    message = " ".join(context.args)
    context.user_data["pending_broadcast"] = message

    preview = box_card(
        "Confirm Broadcast",
        [
            {"type": "section", "emoji": "\U0001F4E2", "heading": "Message Preview"},
            {"type": "text", "content": message},
            {"type": "divider"},
            {"type": "text", "content": "\u26A0\uFE0F This will be sent to ALL users (excluding banned)."},
        ],
        emoji="\U0001F4E2",
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("\u2705 Send to All", callback_data="BC_CONFIRM"),
        InlineKeyboardButton("\u274C Cancel", callback_data="BC_CANCEL"),
    ]])
    await update.message.reply_text(preview, reply_markup=kb, parse_mode="HTML")


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if len(context.args) < 2:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/dm &lt;user_id&gt; &lt;message&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    message = " ".join(context.args[1:])

    text = box_card(
        "Message from Admin",
        [
            {"type": "text", "content": message},
            {"type": "divider"},
            {"type": "text", "content": "- SparkTalks Team"},
        ],
        emoji="\U0001F4E9",
    )
    result = await safe_send(context, target, text, parse_mode="HTML")
    if result:
        await update.message.reply_text(
            box_simple("Sent", f"\u2705 Delivered to <code>{target}</code>.", emoji="\u2705"),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            box_simple("Failed", f"\u274C Could not deliver to <code>{target}</code>.", emoji="\u274C"),
            parse_mode="HTML",
        )
    logger.info(f"ADMIN {update.effective_user.id} -> DM {target}: {message[:80]!r}")


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/forceend &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    u = users.get(target)
    if not u:
        return await update.message.reply_text(
            box_simple("Not Online", f"\u26A0\uFE0F User <code>{target}</code> is offline.", emoji="\U0001F4F4"),
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
                        box_simple("Ended", "\U0001F6D1 Search ended by admin.", emoji="\U0001F6D1"),
                        parse_mode="HTML")
        await update.message.reply_text(
            box_simple("Done", f"\u2705 Search cancelled for <code>{target}</code>.", emoji="\u2705"),
            parse_mode="HTML",
        )
        return
    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text(
            box_simple("Idle", f"\u26A0\uFE0F User <code>{target}</code> is not in a chat.", emoji="\U0001F4A4"),
            parse_mode="HTML",
        )
    await disconnect(context, target, partner, ender_id=target)
    await update.message.reply_text(
        box_simple("Done",
                   f"\u2705 Chat between <code>{target}</code> & <code>{partner}</code> ended.",
                   emoji="\u2705"),
        parse_mode="HTML",
    )


async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Unauthorized!", emoji="\U0001F512"), parse_mode="HTML"
        )
    lines = []
    if users_collection is not None:
        async for doc in users_collection.find(
            {"is_banned": True},
            {"user_id": 1, "name": 1, "username": 1},
        ).limit(50):
            name = doc.get("name") or "-"
            uname = f"@{doc['username']}" if doc.get("username") else "-"
            lines.append(f"\u2022 <code>{doc['user_id']}</code> {name} ({uname})")

    body_text = "No banned users." if not lines else "\n".join(lines)
    body = box_card("Ban List", [{"type": "text", "content": body_text}], emoji="\U0001F6AB")
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Only Owner can promote admins.", emoji="\U0001F512"),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/setadmin &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if target == OWNER_ID:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Owner is already super-admin.", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
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

    await safe_send(context, target,
                    box_simple("Admin Granted", "\U0001F6E1\uFE0F You are now an Admin.", emoji="\U0001F6E1\uFE0F"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"\u2705 <code>{target}</code> is now Admin.", emoji="\u2705"),
        parse_mode="HTML",
    )


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text(
            box_simple("Access Denied", "\u26D4 Only Owner can remove admins.", emoji="\U0001F512"),
            parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Usage: <code>/removeadmin &lt;user_id&gt;</code>", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    try:
        target = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Invalid user ID.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if target == OWNER_ID:
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Cannot remove Owner.", emoji="\u26A0\uFE0F"), parse_mode="HTML"
        )
    if users_collection is None:
        return
    await users_collection.update_one({"user_id": target}, {"$set": {"is_admin": False}})
    u = users.get(target)
    if u:
        u["is_admin"] = False
    admin_cache.discard(target)

    await safe_send(context, target,
                    box_simple("Admin Removed", "\U0001F6E1\uFE0F Your admin access has been revoked.", emoji="\U0001F6E1\uFE0F"),
                    parse_mode="HTML")
    await update.message.reply_text(
        box_simple("Success", f"\u2705 Admin removed from <code>{target}</code>.", emoji="\u2705"),
        parse_mode="HTML",
    )
