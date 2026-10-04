import logging
from telegram import Update
from telegram.ext import ContextTypes

from database import get_user
from utils import box_simple

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
                    box_simple("Access Denied", "\U0001F6AB You have been banned.", emoji="\U0001F6AB"),
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
