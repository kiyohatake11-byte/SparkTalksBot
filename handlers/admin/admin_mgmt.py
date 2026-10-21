"""Admin management: setadmin, removeadmin, adminlist."""
import logging
from telegram import Update, BotCommandScopeChat
from telegram.ext import ContextTypes

from config import OWNER_ID
from state import users, admin_cache
from database import is_owner_or_admin, users_collection, resolve_user
from utils import safe_send, esc
from bot_commands import get_admin_commands, get_user_commands
from handlers.admin._helpers import (
    _sidebar, _deny, _require_db, _not_found,
)

logger = logging.getLogger("sparktalks")


async def cmd_setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /setadmin ID")
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
        return await update.message.reply_text("Usage: /removeadmin ID")
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

    try:
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
        for aid in others:
            u = users.get(aid)
            name = u.get("name") if u else "—"
            lines.append(f"  • <code>{aid}</code> — {esc(name)}")
    await update.message.reply_text(
        _sidebar("Admins", "🛡️", lines), parse_mode="HTML"
    )