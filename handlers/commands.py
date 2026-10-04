from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from state import users
from database import get_user, load_user_from_db, save_user_to_db, create_new_user
from utils import box_card, box_simple, to_bold
from keyboards import (
    get_main_keyboard, get_store_markup, get_profile_text,
    get_settings_text, get_settings_main_kb,
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
        else:
            users[uid] = await create_new_user(uid, user.first_name, user.username)

    users[uid]["name"] = user.first_name
    users[uid]["username"] = user.username
    u = users[uid]

    if u.get("is_banned"):
        return await update.message.reply_text(
            box_simple("Access Denied", "\U0001F6AB You have been banned.", emoji="\U0001F6AB"),
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove(),
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"

    await save_user_to_db(uid, u)

    if u.get("partner"):
        from services.matching import disconnect
        await disconnect(context, uid, u["partner"], ender_id=uid)

    if u.get("gender"):
        if u.get("is_vip"):
            tier = u.get("vip_tier_name") or "VIP"
            vip_status = f"\U0001F451 {tier}"
            blocks = [
                {"type": "line", "content": f"\U0001F44B Hey {to_bold(name)}, welcome back!"},
                {"type": "divider"},
                {"type": "line", "content": f"\u26A1 {to_bold(f'Status : {vip_status}')}"},
            ]
            if u.get("vip_expiry_date"):
                exp_str = u["vip_expiry_date"].strftime("%d %b %Y")
                blocks.append({"type": "line", "content": f"\u231B {to_bold(f'Expires : {exp_str}')}"})
            blocks += [
                {"type": "divider"},
                {"type": "section", "emoji": "\U0001F4CB", "heading": "Quick Commands"},
                {"type": "line", "content": "\U0001F3B2 /next - Find a partner"},
                {"type": "line", "content": "\U0001F6D1 /end - Leave chat"},
                {"type": "line", "content": "\U0001F6CD\uFE0F /buy - VIP Store"},
                {"type": "line", "content": "\u2753 /help - Help guide"},
            ]
        else:
            blocks = [
                {"type": "line", "content": f"\U0001F44B Hey {to_bold(name)}, welcome back!"},
                {"type": "divider"},
                {"type": "line", "content": f"\u26A1 {to_bold('Status : Free Member')}"},
                {"type": "divider"},
                {"type": "section", "emoji": "\U0001F4CB", "heading": "Quick Commands"},
                {"type": "line", "content": "\U0001F3B2 /next - Find a partner"},
                {"type": "line", "content": "\U0001F6D1 /end - Leave chat"},
                {"type": "line", "content": "\U0001F6CD\uFE0F /buy - VIP Store"},
                {"type": "line", "content": "\u2753 /help - Help guide"},
            ]

        dashboard_card = box_card("Dashboard", blocks, emoji="\U0001F3E0")
        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("\U0001F680 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("\U0001F6CD\uFE0F Get VIP", callback_data="BUY_STORE"),
             InlineKeyboardButton("\u2699\uFE0F Settings", callback_data="OPEN_SETTINGS")],
        ])
        await update.message.reply_text(dashboard_card, reply_markup=inline, parse_mode="HTML")
        await update.message.reply_text(
            "Use buttons below \U0001F447", reply_markup=get_main_keyboard()
        )
        return

    blocks = [
        {"type": "text", "content": f"\U0001F48E Hey {to_bold(name)}, welcome!"},
        {"type": "divider"},
        {"type": "section", "emoji": "\u2728", "heading": "Features"},
        {"type": "line", "content": "\U0001F512 Fully private"},
        {"type": "line", "content": "\u26A1 Instant matching worldwide"},
        {"type": "line", "content": "\U0001F6E1\uFE0F Media control + report"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F4CB", "heading": "Commands"},
        {"type": "line", "content": "\U0001F3B2 /next - Find a partner"},
        {"type": "line", "content": "\U0001F6D1 /end - End chat"},
        {"type": "line", "content": "\U0001F6CD\uFE0F /buy - VIP Store"},
        {"type": "line", "content": "\u2753 /help - Full guide"},
        {"type": "divider"},
        {"type": "text", "content": "First, select your gender \U0001F447"},
    ]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("\U0001F468\u200D\U0001F9B1 Male", callback_data="G_MALE"),
        InlineKeyboardButton("\U0001F469\u200D\U0001F9B1 Female", callback_data="G_FEMALE"),
    ]])
    await update.message.reply_text(
        box_card("Quick Setup", blocks, emoji="\u2728"),
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
            box_simple("Cancelled", "\u2705 Action cancelled.", emoji="\u2705"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    await update.message.reply_text(
        box_simple("Nothing to Cancel", "\u2139\uFE0F No pending action.", emoji="\u2139\uFE0F"),
        parse_mode="HTML",
    )


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Please run /start first.", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    await update.message.reply_text(get_profile_text(u), parse_mode="HTML")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            box_simple("Error", "\u26A0\uFE0F Please run /start first.", emoji="\u26A0\uFE0F"),
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
            box_simple("Setup Required", "\u26A0\uFE0F Please run /start first.", emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    text, kb = get_store_markup(u)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocks = [
        {"type": "line", "content": "\u2728 Your complete guide to SparkTalks."},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F3AF", "heading": "Essentials"},
        {"type": "line", "content": "\U0001F680 /start - Dashboard"},
        {"type": "line", "content": "\U0001F3B2 /next - Find partner"},
        {"type": "line", "content": "\U0001F6D1 /end - End chat"},
        {"type": "line", "content": "\u274C /cancel - Cancel action"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F464", "heading": "Profile"},
        {"type": "line", "content": "\U0001F464 /profile - Your profile"},
        {"type": "line", "content": "\u2699\uFE0F /settings - Settings"},
        {"type": "line", "content": "\U0001F6CD\uFE0F /buy - VIP Store"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F6E1\uFE0F", "heading": "Safety"},
        {"type": "line", "content": "\U0001F6A8 /report - Report partner"},
        {"type": "line", "content": "\U0001F6AB /block - Block & skip (VIP only)"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F451", "heading": "VIP Perks"},
        {"type": "line", "content": "\u2022 \U0001F6BB Gender filter"},
        {"type": "line", "content": "\u2022 \u26A1 Priority matching"},
        {"type": "line", "content": "\u2022 \U0001F6AB Block users"},
        {"type": "line", "content": "\u2022 \U0001F451 VIP badge"},
        {"type": "divider"},
        {"type": "line", "content": "\U0001F4A1 Tap a command or use buttons below \U0001F447"},
    ]
    await update.message.reply_text(
        box_card("Help", blocks, emoji="\u2753"), parse_mode="HTML"
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report_internal(context, update.effective_user.id)


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await block_internal(context, update.effective_user.id)
