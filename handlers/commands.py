from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from state import users
from database import get_user, load_user_from_db, save_user_to_db
from utils import box_card, box_simple, to_bold
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
            box_simple("Access Denied", "🚫 You have been banned.", emoji="🚫"),
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"

    await save_user_to_db(uid, u)

    if u.get("partner"):
        from services.matching import disconnect
        await disconnect(context, uid, u["partner"])

        if u.get("gender"):
        if u.get("is_vip"):
            vip_status = f"👑 {u.get('vip_tier_name', 'VIP')}"
            blocks = [
                {"type": "line", "content": f"👋 Hey {to_bold(name)}, welcome back!"},
                {"type": "divider"},
                {"type": "line", "content": f"⚡ {to_bold(f'Status : {vip_status}')}"},
            ]
            if u.get("vip_expiry_date"):
                exp_str = u["vip_expiry_date"].strftime("%d %b %Y")
                blocks.append({"type": "line", "content": f"⌛ {to_bold(f'Expires : {exp_str}')}"})
            blocks += [
                {"type": "divider"},
                {"type": "section", "emoji": "📋", "heading": "Quick Commands"},
                {"type": "line", "content": "🎲 /next — Find a partner"},
                {"type": "line", "content": "🛑 /end — Leave chat"},
                {"type": "line", "content": "🛍️ /buy — VIP Store"},
                {"type": "line", "content": "❓ /help — Help guide"},
            ]
        else:
            blocks = [
                {"type": "line", "content": f"👋 Hey {to_bold(name)}, welcome back!"},
                {"type": "divider"},
                {"type": "line", "content": f"⚡ {to_bold('Status : ⚪ Free Member')}"},
                {"type": "divider"},
                {"type": "section", "emoji": "📋", "heading": "Quick Commands"},
                {"type": "line", "content": "🎲 /next — Find a partner"},
                {"type": "line", "content": "🛑 /end — Leave chat"},
                {"type": "line", "content": "🛍️ /buy — VIP Store"},
                {"type": "line", "content": "❓ /help — Help guide"},
            ]

        dashboard_card = box_card("Dashboard", blocks, emoji="🏠")

        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
             InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")]
        ])
        await update.message.reply_text(
            dashboard_card, reply_markup=inline, parse_mode="HTML"
        )
        await update.message.reply_text(" ", reply_markup=get_main_keyboard())
        return

    blocks = [
        {"type": "text", "content": f"💎 Hey {name}, welcome to SparkTalks!"},
        {"type": "divider"},
        {"type": "text", "content": "Talk to strangers anonymously 🎭"},
        {"type": "divider"},
        {"type": "section", "emoji": "✨", "heading": "Features"},
        {"type": "line", "content": "🔒 Fully private"},
        {"type": "line", "content": "⚡ Instant matching worldwide"},
        {"type": "line", "content": "🛡️ Media control + report/block"},
        {"type": "divider"},
        {"type": "section", "emoji": "📋", "heading": "Commands"},
        {"type": "line", "content": "🎲 /next — Find a partner"},
        {"type": "line", "content": "🛑 /end — End chat"},
        {"type": "line", "content": "🛍️ /buy — VIP Store"},
        {"type": "line", "content": "❓ /help — Full guide"},
        {"type": "divider"},
        {"type": "text", "content": "First, select your gender:"},
    ]
    welcome_card = box_card("Get Started", blocks, emoji="✨")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
        InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE")
    ]])
    await update.message.reply_text(welcome_card, reply_markup=kb, parse_mode="HTML")


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await try_match(context, update.effective_user.id)


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await end_chat_internal(context, update.effective_user.id)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Please run /start first.", emoji="⚠️"), parse_mode="HTML"
        )
    await update.message.reply_text(get_profile_text(u), parse_mode="HTML")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Please run /start first.", emoji="⚠️"), parse_mode="HTML"
        )
    await update.message.reply_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Setup Required", "⚠️ Please run /start first.", emoji="⚠️"), parse_mode="HTML"
        )
    text, kb = get_store_markup(u)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocks = [
        {"type": "section", "emoji": "📋", "heading": "Commands"},
        {"type": "line", "content": "🚀 /start — Dashboard"},
        {"type": "line", "content": "🎲 /next — Find partner"},
        {"type": "line", "content": "🛑 /end — End chat"},
        {"type": "line", "content": "👤 /profile — Your profile"},
        {"type": "line", "content": "⚙️ /settings — Settings"},
        {"type": "line", "content": "🛍️ /buy — VIP Store"},
        {"type": "line", "content": "🚨 /report — Report partner"},
        {"type": "line", "content": "🚫 /block — Block & skip"},
        {"type": "line", "content": "❓ /help — This guide"},
        {"type": "divider"},
        {"type": "text", "content": "Tap a command or use buttons below 👇"},
    ]
    await update.message.reply_text(
        box_card("Help", blocks, emoji="❓"), parse_mode="HTML"
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report_internal(context, update.effective_user.id)


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await block_internal(context, update.effective_user.id)