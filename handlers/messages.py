import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    BTN_FIND, BTN_SETTINGS, BTN_VIP, BTN_PROFILE,
    BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK, MAX_PENDING_MEDIA, MAX_REACTION_ENTRIES
)
from state import users, message_reactions_map
from database import get_user, save_user_to_db
from utils import spark_card, safe_send, split_message
from keyboards import get_main_keyboard, get_settings_text, get_settings_main_kb
from handlers.commands import (
    cmd_next, cmd_settings, cmd_buy, cmd_profile,
    cmd_end, cmd_report, cmd_block
)

logger = logging.getLogger("sparktalks")


async def relay_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    u = await get_user(uid)
    if u and u.get("is_banned"):
        return

    if msg.text:
        text = msg.text.strip()
        if text == BTN_FIND:
            return await cmd_next(update, context)
        if text == BTN_SETTINGS:
            return await cmd_settings(update, context)
        if text == BTN_VIP:
            return await cmd_buy(update, context)
        if text == BTN_PROFILE:
            return await cmd_profile(update, context)
        if text == BTN_NEXT:
            return await cmd_next(update, context)
        if text == BTN_END:
            return await cmd_end(update, context)
        if text == BTN_REPORT:
            return await cmd_report(update, context)
        if text == BTN_BLOCK:
            return await cmd_block(update, context)

    if u and u.get("awaiting_input") == "bio":
        if msg.text:
            u["bio"] = msg.text[:120]
            u["awaiting_input"] = None
            await save_user_to_db(uid, u)
            await msg.reply_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")
        else:
            await msg.reply_text("⚠️ Please send text for bio.")
        return

    if not u or not u.get("partner"):
        name = u.get("name") if u else "there"
        return await msg.reply_text(
            spark_card("Not connected", f"💡 Hey {name}, you're not in a chat right now.\n\nTap <b>Find Partner</b> or send /next to start on SparkTalks."),
            parse_mode="HTML", reply_markup=get_main_keyboard()
        )

    pid = u["partner"]
    partner = users.get(pid)

    if msg.text:
        parts = split_message(msg.text)
        first_sent = None
        for part in parts:
            sent = await safe_send(context, pid, part)
            if sent and first_sent is None:
                first_sent = sent
        if first_sent:
            message_reactions_map[f"{pid}:{first_sent.message_id}"] = {"target_chat": uid, "target_msg": msg.message_id}
            message_reactions_map[f"{uid}:{msg.message_id}"] = {"target_chat": pid, "target_msg": first_sent.message_id}
            while len(message_reactions_map) > MAX_REACTION_ENTRIES:
                message_reactions_map.popitem(last=False)

    elif msg.photo or msg.video or msg.voice or msg.sticker or msg.document:
        if partner and partner.get("confirm_media", True):
            pm_map = partner.get("pending_media") or {}
            pm_map[msg.message_id] = {"from_id": uid, "msg_id": msg.message_id}

            if len(pm_map) > MAX_PENDING_MEDIA:
                oldest = next(iter(pm_map))
                pm_map.pop(oldest, None)

            partner["pending_media"] = pm_map
            media_type = (
                "Photo" if msg.photo else
                "Video" if msg.video else
                "Voice" if msg.voice else
                "Sticker" if msg.sticker else "Document"
            )
            prompt = f"📩 Incoming {media_type.lower()}. Accept?"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("👁️ Accept", callback_data=f"MEDIA_ACCEPT:{msg.message_id}"),
                InlineKeyboardButton("🚫 Decline", callback_data=f"MEDIA_DECLINE:{msg.message_id}")
            ]])
            await safe_send(context, pid, spark_card("Media", prompt, "Media Shield"),
                            reply_markup=kb, parse_mode="HTML")
            await msg.reply_text("⏳ Waiting for partner approval...")
        else:
            try:
                sent = await context.bot.copy_message(chat_id=pid, from_chat_id=uid, message_id=msg.message_id)
                if sent:
                    message_reactions_map[f"{pid}:{sent.message_id}"] = {"target_chat": uid, "target_msg": msg.message_id}
                    message_reactions_map[f"{uid}:{msg.message_id}"] = {"target_chat": pid, "target_msg": sent.message_id}
                    while len(message_reactions_map) > MAX_REACTION_ENTRIES:
                        message_reactions_map.popitem(last=False)
            except Exception as e:
                logger.error(f"Media relay error: {e}")


async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not reaction:
        return
    key = f"{reaction.chat.id}:{reaction.message_id}"
    mapping = message_reactions_map.get(key)
    if mapping:
        try:
            await context.bot.set_message_reaction(
                chat_id=mapping["target_chat"],
                message_id=mapping["target_msg"],
                reaction=reaction.new_reaction
            )
        except Exception as e:
            logger.error(f"Reaction sync error: {e}")
