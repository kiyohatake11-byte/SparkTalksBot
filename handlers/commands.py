from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from state import users
from database import get_user, load_user_from_db, save_user_to_db
from utils import spark_card, to_bold, to_smallcaps
from keyboards import (
    get_main_keyboard, get_store_markup, get_profile_text,
    get_settings_text, get_settings_main_kb
)
from services.matching import try_match, end_chat_internal, report_internal, block_internal


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    name = user.first_name or "there"

    if uid not in users:
        db_user = await load_user_from_db(uid)
        if db_user:
            users[uid] = db_user
            users[uid]["name"] = user.first_name
            users[uid]["username"] = user.username
        else:
            users[uid] = {
                "name": user.first_name, "username": user.username,
                "gender": None, "age": None, "country": None, "bio": None,
                "interests": [], "profile_public": False,
                "confirm_media": True, "pref_gender": "Any",
                "is_vip": False, "vip_expiry_date": None, "vip_tier_name": "None",
                "is_admin": False, "is_banned": False,
                "blocked_users": [],
                "state": "IDLE", "partner": None, "temp": None,
                "pending_media": {}, "awaiting_input": None, "recent_partners": [],
                "last_active": None,
            }
    else:
        users[uid]["name"] = user.first_name
        users[uid]["username"] = user.username

    u = users[uid]
    if u.get("is_banned"):
        return await update.message.reply_text(
            spark_card("Access Denied", "🚫 You have been banned."),
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"

    await save_user_to_db(uid, u)

    if u.get("partner"):
        from services.matching import disconnect
        await disconnect(context, uid, u["partner"])

    if u.get("gender"):
        vip_status = f"⭐ {u.get('vip_tier_name', 'VIP')}" if u.get("is_vip") else "Free"
        body = (
            f"👋 <b>Hey {name}, welcome back!</b>\n\n"
            f"🌟 <b>Membership:</b> {vip_status}\n\n"
            f"❖ <b>{to_bold('Quick Commands')}</b>\n"
            f"  🎲 /next — Find a partner\n"
            f"  🛑 /end — Leave chat\n"
            f"  🛍️ /buy — VIP Store\n"
            f"  ❓ /help — Help guide"
        )
        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
             InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")]
        ])
        await update.message.reply_text(
            spark_card("Dashboard", body, "Ready to chat"),
            reply_markup=inline, parse_mode="HTML"
        )
        await update.message.reply_text(
            " ",
            reply_markup=get_main_keyboard()
        )
        return

    body = (
        f"💎 <b>Hey {name}, welcome to SparkTalks!</b>\n\n"
        f"<i>Talk to strangers anonymously</i> 🎭\n\n"
        f"❖ <b>{to_bold('Features')}</b>\n"
        f"  🔒 Fully private\n"
        f"  ⚡ Instant matching worldwide\n"
        f"  🛡️ Media control + report/block\n\n"
        f"❖ <b>{to_bold('Useful Commands')}</b>\n"
        f"  🎲 /next — Find a partner\n"
        f"  🛑 /end — End chat\n"
        f"  🛍️ /buy — VIP Store\n"
        f"  ❓ /help — Full guide\n\n"
        f"<i>First, select your gender to get started:</i>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
        InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE")
    ]])
    await update.message.reply_text(
        spark_card("Get Started", body, "SparkTalks"),
        reply_markup=kb, parse_mode="HTML"
    )


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await try_match(context, update.effective_user.id)


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await end_chat_internal(context, update.effective_user.id)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            spark_card("Error", "⚠️ Please run /start first."), parse_mode="HTML"
        )
    await update.message.reply_text(get_profile_text(u), parse_mode="HTML")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            spark_card("Error", "⚠️ Please run /start first."), parse_mode="HTML"
        )
    await update.message.reply_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            spark_card("Setup Required", "⚠️ Please run /start first."), parse_mode="HTML"
        )
    text, kb = get_store_markup(u)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = (
        f"✨ <b>{to_bold('SparkTalks Help')}</b>\n\n"
        f"❖ <b>{to_bold('Commands')}</b>\n"
        f"  🚀 /start — Dashboard\n"
        f"  🎲 /next — Find partner\n"
        f"  🛑 /end — End chat\n"
        f"  👤 /profile — Your profile\n"
        f"  ⚙️ /settings — Settings\n"
        f"  🛍️ /buy — VIP Store\n"
        f"  🚨 /report — Report partner\n"
        f"  🚫 /block — Block & skip\n"
        f"  ❓ /help — This guide\n\n"
        f"<i>Tap a command or use the buttons below 👇</i>"
    )
    await update.message.reply_text(
        spark_card("Help", body, "SparkTalks"), parse_mode="HTML"
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report_internal(context, update.effective_user.id)


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await block_internal(context, update.effective_user.id)