import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove,
    LinkPreviewOptions,
)
from telegram.ext import ContextTypes

from state import users
from database import get_user, load_user_from_db, save_user_to_db, create_new_user
from utils import box_card, box_simple, to_bold
from keyboards import (
    get_main_keyboard, get_store_markup, get_profile_text,
    get_settings_text, get_settings_main_kb,
)
from services.matching import try_match, end_chat_internal, report_internal, block_internal

logger = logging.getLogger("sparktalks")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    name = user.first_name or "there"

    logger.info(f"🔍 cmd_start triggered | user={uid} | args={context.args}")

    if uid not in users:
        db_user = await load_user_from_db(uid)
        if db_user:
            users[uid] = db_user
        else:
            users[uid] = await create_new_user(uid, user.first_name, user.username)

    users[uid]["name"] = user.first_name
    users[uid]["username"] = user.username
    u = users[uid]

    if u.get("is_banned"):
        return await update.message.reply_text(
            box_simple("Access Denied", "🚫 You have been banned.", emoji="🚫"),
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove(),
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"

    await save_user_to_db(uid, u)

    # ═══════════════════════════════════════════════════════════
    # 🆕 DEEP LINK HANDLING
    # ═══════════════════════════════════════════════════════════
    if context.args:
        payload = context.args[0].lower().strip()
        logger.info(f"🔗 Deep link payload: {payload!r}")

        if payload in ("vip", "buy", "store"):
            try:
                text, kb = get_store_markup(u)
                logger.info(f"🛍️ Sending VIP Store to {uid}")
                return await update.message.reply_text(
                    text, reply_markup=kb, parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except Exception as e:
                logger.error(f"❌ VIP Store failed: {e}", exc_info=True)
                return await update.message.reply_text(
                    "⚠️ VIP Store could not open. Please try /buy command.",
                    parse_mode="HTML",
                )

        if payload == "settings" and u.get("gender"):
            return await update.message.reply_text(
                get_settings_text(u),
                reply_markup=get_settings_main_kb(u),
                parse_mode="HTML",
            )

        if payload == "help":
            return await cmd_help(update, context)
    # ═══════════════════════════════════════════════════════════

    if u.get("partner"):
        from services.matching import disconnect
        await disconnect(context, uid, u["partner"], ender_id=uid)

    if u.get("gender"):
        if u.get("is_vip"):
            tier = u.get("vip_tier_name") or "VIP"
            vip_status = f"👑 {tier}"
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
             InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")],
        ])
        await update.message.reply_text(dashboard_card, reply_markup=inline, parse_mode="HTML")
        await update.message.reply_text(
            "Use buttons below 👇", reply_markup=get_main_keyboard()
        )
        return

    blocks = [
        {"type": "text", "content": f"💎 Hey {to_bold(name)}, welcome!"},
        {"type": "divider"},
        {"type": "section", "emoji": "✨", "heading": "Features"},
        {"type": "line", "content": "🔒 Fully private"},
        {"type": "line", "content": "⚡ Instant matching worldwide"},
        {"type": "line", "content": "🛡️ Media control + report"},
        {"type": "divider"},
        {"type": "section", "emoji": "📋", "heading": "Commands"},
        {"type": "line", "content": "🎲 /next — Find a partner"},
        {"type": "line", "content": "🛑 /end — End chat"},
        {"type": "line", "content": "🛍️ /buy — VIP Store"},
        {"type": "line", "content": "❓ /help — Full guide"},
        {"type": "divider"},
        {"type": "text", "content": "First, select your gender 👇"},
    ]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
        InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
    ]])
    await update.message.reply_text(
        box_card("Quick Setup", blocks, emoji="✨"),
        reply_markup=kb, parse_mode="HTML",
    )


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await try_match(context, update.effective_user.id)


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await end_chat_internal(context, update.effective_user.id)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    if u.get("awaiting_input"):
        u["awaiting_input"] = None
        await save_user_to_db(uid, u)
        return await update.message.reply_text(
            box_simple("Cancelled", "✅ Action cancelled.", emoji="✅"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    await update.message.reply_text(
        box_simple("Nothing to Cancel", "ℹ️ No pending action.", emoji="ℹ️"),
        parse_mode="HTML",
    )


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Please run /start first.", emoji="⚠️"),
            parse_mode="HTML",
        )
    await update.message.reply_text(get_profile_text(u), parse_mode="HTML")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Error", "⚠️ Please run /start first.", emoji="⚠️"),
            parse_mode="HTML",
        )
    await update.message.reply_text(
        get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML"
    )


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Setup Required", "⚠️ Please run /start first.", emoji="⚠️"),
            parse_mode="HTML",
        )
    text, kb = get_store_markup(u)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocks = [
        {"type": "line", "content": "✨ Your complete guide to SparkTalks."},
        {"type": "divider"},
        {"type": "section", "emoji": "🎯", "heading": "Essentials"},
        {"type": "line", "content": "🚀 /start — Dashboard"},
        {"type": "line", "content": "🎲 /next — Find partner"},
        {"type": "line", "content": "🛑 /end — End chat"},
        {"type": "line", "content": "❌ /cancel — Cancel action"},
        {"type": "divider"},
        {"type": "section", "emoji": "👤", "heading": "Profile"},
        {"type": "line", "content": "👤 /profile — Your profile"},
        {"type": "line", "content": "⚙️ /settings — Settings"},
        {"type": "line", "content": "🛍️ /buy — VIP Store"},
        {"type": "divider"},
        {"type": "section", "emoji": "🛡️", "heading": "Safety"},
        {"type": "line", "content": "🚨 /report — Report partner"},
        {"type": "line", "content": "🚫 /block — Block & skip (VIP only)"},
        {"type": "divider"},
        {"type": "section", "emoji": "👑", "heading": "VIP Perks"},
        {"type": "line", "content": "• 🚻 Gender filter"},
        {"type": "line", "content": "• ⚡ Priority matching"},
        {"type": "line", "content": "• 🚫 Block users"},
        {"type": "line", "content": "• 👑 VIP badge"},
        {"type": "divider"},
        {"type": "line", "content": "💡 Tap a command or use buttons below 👇"},
    ]
    await update.message.reply_text(
        box_card("Help", blocks, emoji="❓"), parse_mode="HTML"
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report_internal(context, update.effective_user.id)


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await block_internal(context, update.effective_user.id)