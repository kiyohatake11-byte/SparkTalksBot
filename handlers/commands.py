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


# ══════════════════════════════════════════════════════════════
# 🏠 DASHBOARD — Sidebar Style
# ══════════════════════════════════════════════════════════════

def _build_dashboard(u: dict, name: str) -> str:
    if u.get("is_vip"):
        tier = u.get("vip_tier_name") or "VIP"
        status = f"👑 {tier}"
    else:
        status = "⚪ Free Member"

    lines = [
        "🏠  ✨  <b>Dashboard</b>  ✨  🏠",
        "▎",
        f"▎ 👋 Hey <b>{name}</b>, welcome back!",
        "▎",
        "▎ 👤  <b>Your Account</b>",
        f"▎   ├ ⚡ Status : {status}",
    ]

    if u.get("is_vip") and u.get("vip_expiry_date"):
        exp_str = u["vip_expiry_date"].strftime("%d %b %Y")
        lines.append(f"▎   ├ ⌛ Expires : {exp_str}")

    lines += [
        f"▎   ├ 📊 Chats : {u.get('total_chats', 0)}",
        f"▎   └ 🏆 Matches : {u.get('total_matches', 0)}",
        "▎",
        "▎ ⚡  <b>Quick Actions</b>",
        "▎   ├ 🎲 /next   — Find a partner",
        "▎   ├ 🛑 /end    — Leave chat",
        "▎   ├ 🛍️ /buy    — VIP Store",
        "▎   └ ❓ /help   — Help guide",
        "▎",
        "",
    ]

    if u.get("is_vip"):
        lines.append("💎 <i>Thanks for supporting SparkTalks!</i>")
    else:
        lines.append("💡 <i>/buy to unlock VIP perks!</i>")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 🎉 ONBOARDING — Sidebar Style
# ══════════════════════════════════════════════════════════════

def _build_onboarding(name: str) -> str:
    return (
        "📜  ✨  <b>Quick Setup</b>  ✨  📜\n"
        "▎\n"
        f"▎ 💎 Hey <b>{name}</b>, welcome to SparkTalks!\n"
        "▎\n"
        "▎ ✨  <b>Features</b>\n"
        "▎   ├ 🔒 Fully private\n"
        "▎   ├ ⚡ Instant matching\n"
        "▎   └ 🛡️ Media control + report\n"
        "▎\n"
        "▎ 📋  <b>Commands</b>\n"
        "▎   ├ 🎲 /next   — Find a partner\n"
        "▎   ├ 🛑 /end    — End chat\n"
        "▎   ├ 🛍️ /buy    — VIP Store\n"
        "▎   └ ❓ /help   — Full guide\n"
        "▎\n"
        "💡 <i>First, select your gender 👇</i>"
    )


# ══════════════════════════════════════════════════════════════
# CMD_START
# ══════════════════════════════════════════════════════════════

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
            "🚫  ✨  <b>Access Denied</b>  ✨  🚫\n"
            "▎\n"
            "▎ You have been banned.\n"
            "▎\n"
            "💡 <i>Contact support if this is a mistake.</i>",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove(),
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"

    await save_user_to_db(uid, u)

    # ═══════════════════════════════════════════════════════════
    # DEEP LINK HANDLING
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

    # ─── Disconnect if already in chat ───
    if u.get("partner"):
        from services.matching import disconnect
        await disconnect(context, uid, u["partner"], ender_id=uid)

    # ─── Existing user → Dashboard ───
    if u.get("gender"):
        dashboard_card = _build_dashboard(u, name)
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

    # ─── New user → Onboarding ───
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
        InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
    ]])
    await update.message.reply_text(
        _build_onboarding(name),
        reply_markup=kb, parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════
# OTHER COMMANDS
# ══════════════════════════════════════════════════════════════

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
            "✅  ✨  <b>Cancelled</b>  ✨  ✅\n"
            "▎\n"
            "▎ Action cancelled successfully.\n"
            "▎\n"
            "💡 <i>Ready to continue!</i>",
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    await update.message.reply_text(
        "ℹ️  ✨  <b>Nothing to Cancel</b>  ✨  ℹ️\n"
        "▎\n"
        "▎ No pending action.\n"
        "▎\n"
        "💡 <i>Use /help for commands.</i>",
        parse_mode="HTML",
    )


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            "⚠️  ✨  <b>Setup Required</b>  ✨  ⚠️\n"
            "▎\n"
            "▎ Please run /start first.\n"
            "▎\n"
            "💡 <i>Quick setup takes 10 seconds!</i>",
            parse_mode="HTML",
        )
    await update.message.reply_text(get_profile_text(u), parse_mode="HTML")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            "⚠️  ✨  <b>Setup Required</b>  ✨  ⚠️\n"
            "▎\n"
            "▎ Please run /start first.\n"
            "▎\n"
            "💡 <i>Quick setup takes 10 seconds!</i>",
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
            "⚠️  ✨  <b>Setup Required</b>  ✨  ⚠️\n"
            "▎\n"
            "▎ Please run /start first.\n"
            "▎\n"
            "💡 <i>Quick setup takes 10 seconds!</i>",
            parse_mode="HTML",
        )
    text, kb = get_store_markup(u)
    await update.message.reply_text(
        text, reply_markup=kb, parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓  ✨  <b>Help</b>  ✨  ❓\n"
        "▎\n"
        "▎ 🎯  <b>Essentials</b>\n"
        "▎   ├ 🚀 /start  — Dashboard\n"
        "▎   ├ 🎲 /next   — Find partner\n"
        "▎   ├ 🛑 /end    — End chat\n"
        "▎   └ ❌ /cancel — Cancel action\n"
        "▎\n"
        "▎ 👤  <b>Profile</b>\n"
        "▎   ├ 👤 /profile  — Your profile\n"
        "▎   ├ ⚙️ /settings — Settings\n"
        "▎   └ 🛍️ /buy      — VIP Store\n"
        "▎\n"
        "▎ 🛡️  <b>Safety</b>\n"
        "▎   ├ 🚨 /report — Report partner\n"
        "▎   └ 🚫 /block  — Block (VIP)\n"
        "▎\n"
        "▎ 👑  <b>VIP Perks</b>\n"
        "▎   ├ 🚻 Gender filter\n"
        "▎   ├ ⚡ Priority matching\n"
        "▎   ├ 🚫 Block users\n"
        "▎   └ 👑 VIP badge\n"
        "▎\n"
        "💡 <i>Tap any command below!</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report_internal(context, update.effective_user.id)


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await block_internal(context, update.effective_user.id)