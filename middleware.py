import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

from database import get_user, is_owner_or_admin
from utils import box_simple, safe_send

logger = logging.getLogger("sparktalks")


async def get_verified_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return None
    u = await get_user(user.id)
    if not u:
        return None
    if u.get("is_banned"):
        msg = update.effective_message
        if msg:
            try:
                await msg.reply_text(
                    box_simple("Access Denied", "🚫 You have been banned.", emoji="🚫"),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        elif update.callback_query:
            try:
                await update.callback_query.answer("You are banned.", show_alert=True)
            except Exception:
                pass
        return None
    return u


def require_admin(func):
    """Decorator: only admin/owner can run the command."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id if update.effective_user else 0
        if not await is_owner_or_admin(uid):
            if update.message:
                try:
                    await update.message.reply_text("⛔ Admin only.", parse_mode="HTML")
                except Exception:
                    pass
            elif update.callback_query:
                await update.callback_query.answer("Admin only.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_owner(func):
    """Decorator: only owner can run."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        from config import OWNER_ID
        uid = update.effective_user.id if update.effective_user else 0
        if uid != OWNER_ID:
            if update.message:
                try:
                    await update.message.reply_text("⛔ Owner only.", parse_mode="HTML")
                except Exception:
                    pass
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_vip(func):
    """Decorator: only VIP can run."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        from database import get_user
        uid = update.effective_user.id if update.effective_user else 0
        u = await get_user(uid)
        if not u or not u.get("is_vip"):
            if update.message:
                try:
                    await update.message.reply_text("👑 VIP only.", parse_mode="HTML")
                except Exception:
                    pass
            return
        return await func(update, context, *args, **kwargs)
    return wrapper