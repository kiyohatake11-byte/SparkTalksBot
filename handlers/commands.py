import logging
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove,
    LinkPreviewOptions,
)
from telegram.ext import ContextTypes

from config import TIMEZONE_OPTIONS, SUPPORTED_LANGUAGES
from state import users, analytics
from database import get_user, load_user_from_db, save_user_to_db, create_new_user
from utils import box_card, box_simple, utcnow, user_local_time
from keyboards import (
    get_main_keyboard, get_store_markup, get_profile_text,
    get_settings_text, get_settings_main_kb,
    get_language_kb, get_timezone_kb, get_theme_kb,
)
from services.matching import (
    try_match, end_chat_internal, report_internal, block_internal,
)

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# TIME-BASED GREETING (FIXED — uses user timezone)
# ══════════════════════════════════════════════════════════════

def _get_time_greeting(u: dict = None) -> dict:
    """Return greeting based on the USER's local time, not server time."""
    offset = (u or {}).get("timezone_offset", 5.5)
    user_now = user_local_time(offset)
    hour = user_now.hour

    if 5 <= hour < 12:
        return {
            "greeting": "Good Morning",
            "title_left": "🌅", "title_right": "🌅", "title_mid": "☀️",
            "emoji": "🌅",
            "tip_dashboard": "🌅 <i>Fresh start — find your first match today!</i>",
            "tip_onboarding": "🌅 <i>First, select your gender 👇</i>",
        }
    elif 12 <= hour < 17:
        return {
            "greeting": "Good Afternoon",
            "title_left": "☀️", "title_right": "☀️", "title_mid": "🌤️",
            "emoji": "☀️",
            "tip_dashboard": "☀️ <i>Great time to find new friends!</i>",
            "tip_onboarding": "☀️ <i>First, select your gender 👇</i>",
        }
    elif 17 <= hour < 22:
        return {
            "greeting": "Good Evening",
            "title_left": "🌆", "title_right": "🌆", "title_mid": "🌙",
            "emoji": "🌆",
            "tip_dashboard": "🌙 <i>Prime time — most users online now!</i>",
            "tip_onboarding": "🌆 <i>First, select your gender 👇</i>",
        }
    else:
        return {
            "greeting": "Good Night",
            "title_left": "🌙", "title_right": "🌙", "title_mid": "✨",
            "emoji": "🌙",
            "tip_dashboard": "🌙 <i>Night owls — you'll find someone special!</i>",
            "tip_onboarding": "🌙 <i>First, select your gender 👇</i>",
        }


def _build_dashboard(u: dict, name: str) -> str:
    tg = _get_time_greeting(u)

    if u.get("is_vip"):
        tier = u.get("vip_tier_name") or "VIP"
        status = f"👑 {tier}"
    else:
        status = "⚪ Free Member"

    title = (
        f"{tg['title_left']}  {tg['title_mid']}  "
        f"<b>{tg['greeting']}, {name}!</b>  "
        f"{tg['title_mid']}  {tg['title_right']}"
    )

    lines = [
        title,
        "▎",
        "▎ 💬 <i>Chat anonymously with strangers instantly!</i>",
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
        "▎   ├ 🎁 /invite — Invite friends",
        "▎   ├ 🛍️ /buy    — VIP Store",
        "▎   └ ❓ /help   — Help guide",
        "▎",
        "",
    ]
    if u.get("is_vip"):
        lines.append("💎 <i>Thanks for supporting SparkTalks!</i>")
    else:
        lines.append(tg["tip_dashboard"])
    return "\n".join(lines)


def _build_onboarding(name: str, u: dict = None) -> str:
    tg = _get_time_greeting(u)
    title = f"{tg['title_left']}  ✨  <b>Welcome to SparkTalks</b>  ✨  {tg['title_right']}"
    return (
        f"{title}\n"
        "▎\n"
        "▎ 💬 <i>Chat anonymously with strangers instantly!</i>\n"
        "▎\n"
        f"▎ {tg['emoji']} Hey <b>{name}</b>, welcome aboard!\n"
        "▎\n"
        "▎ ✨  <b>Features</b>\n"
        "▎   ├ 🔒 Fully private\n"
        "▎   ├ ⚡ Instant matching\n"
        "▎   ├ 🌍 Worldwide partners\n"
        "▎   ├ 🎙️ Voice rooms (VIP)\n"
        "▎   └ 🛡️ Media control + report\n"
        "▎\n"
        "▎ 📋  <b>Commands</b>\n"
        "▎   ├ 🎲 /next   — Find a partner\n"
        "▎   ├ 🛑 /end    — End chat\n"
        "▎   ├ 🛍️ /buy    — VIP Store\n"
        "▎   └ ❓ /help   — Full guide\n"
        "▎\n"
        f"{tg['tip_onboarding']}"
    )


# ══════════════════════════════════════════════════════════════
# CORE COMMANDS
# ══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    name = user.first_name or "there"

    logger.info(f"cmd_start | user={uid} | args={context.args}")

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

    # AUTO TIMEZONE by country
    if not u.get("timezone_set_manually"):
        country = u.get("country") or ""
        if "India" in country:
            u["timezone_offset"] = 5.5
        elif "Pakistan" in country:
            u["timezone_offset"] = 5.0
        elif "USA" in country:
            u["timezone_offset"] = -5.0
        elif "UK" in country:
            u["timezone_offset"] = 0.0
        elif "Canada" in country:
            u["timezone_offset"] = -5.0
        elif "UAE" in country or "Gulf" in country:
            u["timezone_offset"] = 4.0
        elif "Nepal" in country:
            u["timezone_offset"] = 5.75
        elif "Moscow" in country:
            u["timezone_offset"] = 3.0

    await save_user_to_db(uid, u)

    if context.args:
        payload = context.args[0].lower().strip()
        logger.info(f"Deep link: {payload!r}")

        # Referral
        if payload.startswith("ref_"):
            from services.referral import handle_referral_payload
            applied = await handle_referral_payload(context, uid, payload)
            if applied:
                await update.message.reply_text(
                    "🎁 Referral applied! Welcome to SparkTalks.",
                    parse_mode="HTML",
                )

        if payload in ("vip", "buy", "store"):
            try:
                text, kb = get_store_markup(u)
                return await update.message.reply_text(
                    text, reply_markup=kb, parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except Exception as e:
                logger.error(f"VIP Store failed: {e}", exc_info=True)
                return await update.message.reply_text(
                    "⚠️ VIP Store could not open. Try /buy.", parse_mode="HTML",
                )

        if payload == "settings" and u.get("gender"):
            return await update.message.reply_text(
                get_settings_text(u),
                reply_markup=get_settings_main_kb(u),
                parse_mode="HTML",
            )

        if payload == "help":
            return await cmd_help(update, context)

    if u.get("partner"):
        from services.matching import disconnect
        await disconnect(context, uid, u["partner"], ender_id=uid)

    if u.get("gender"):
        dashboard_card = _build_dashboard(u, name)
        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
             InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")],
            [InlineKeyboardButton("🎁 Invite & Earn", callback_data="OPEN_INVITE")],
        ])
        await update.message.reply_text(dashboard_card, reply_markup=inline, parse_mode="HTML")
        await update.message.reply_text("Use buttons below 👇", reply_markup=get_main_keyboard())
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
        InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
    ]])
    await update.message.reply_text(_build_onboarding(name, u), reply_markup=kb, parse_mode="HTML")


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
            "▎\n▎ Action cancelled.\n▎\n💡 <i>Ready to continue!</i>",
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    await update.message.reply_text(
        "ℹ️  ✨  <b>Nothing to Cancel</b>  ✨  ℹ️\n"
        "▎\n▎ No pending action.\n▎\n💡 <i>Use /help for commands.</i>",
        parse_mode="HTML",
    )


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            "⚠️  ✨  <b>Setup Required</b>  ✨  ⚠️\n"
            "▎\n▎ Please run /start first.\n▎\n💡 <i>Quick setup takes 10 seconds!</i>",
            parse_mode="HTML",
        )
    await update.message.reply_text(get_profile_text(u), parse_mode="HTML")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            "⚠️ Please /start first.", parse_mode="HTML",
        )
    await update.message.reply_text(
        get_settings_text(u),
        reply_markup=get_settings_main_kb(u),
        parse_mode="HTML",
    )


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text(
            "⚠️ Please /start first.", parse_mode="HTML",
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
        "▎   ├ 🎨 /theme    — Chat theme\n"
        "▎   ├ 🕐 /timezone — Set timezone\n"
        "▎   ├ 🌐 /language — Change language\n"
        "▎   └ 🛍️ /buy      — VIP Store\n"
        "▎\n"
        "▎ 🎁  <b>Rewards</b>\n"
        "▎   ├ 🎁 /invite — Invite & earn\n"
        "▎   └ ⭐ /myvip  — Your VIP status\n"
        "▎\n"
        "▎ 🛡️  <b>Safety</b>\n"
        "▎   ├ 🚨 /report — Report partner\n"
        "▎   └ 🚫 /block  — Block (VIP)\n"
        "▎\n"
        "▎ 👑  <b>VIP Perks</b>\n"
        "▎   ├ 🚻 Gender filter\n"
        "▎   ├ 🎙️ Voice rooms\n"
        "▎   ├ ⚡ Priority matching\n"
        "▎   └ 🚫 Block users\n"
        "▎\n"
        "▎ 🛡️ <i>Links & usernames are auto-blocked in chats.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await report_internal(context, update.effective_user.id)


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await block_internal(context, update.effective_user.id)


# ══════════════════════════════════════════════════════════════
# NEW COMMANDS
# ══════════════════════════════════════════════════════════════

async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("gender"):
        return await update.message.reply_text("⚠️ Please /start first.")
    from services.referral import send_invite
    await send_invite(update, context, u)


async def cmd_myvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    history = u.get("payment_history") or []

    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y") if exp else "—"
        status = f"👑 {u.get('vip_tier_name', 'VIP')}"
    else:
        status = "⚪ Free Member"
        exp_str = "—"

    lines = [
        "⭐  ✨  <b>Your VIP Status</b>  ✨  ⭐",
        "▎",
        f"▎ Status : {status}",
        f"▎ Expires : {exp_str}",
    ]
    if history:
        lines.append("▎")
        lines.append("▎ 🧾  <b>Recent Purchases</b>")
        for h in reversed(history[-5:]):
            lines.append(f"▎   • {h.get('tier', 'VIP')} — {h.get('at', '')[:10]}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_theme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    await update.message.reply_text(
        "🎨  ✨  <b>Chat Theme</b>  ✨  🎨\n\nPick a theme for your match cards:",
        reply_markup=get_theme_kb(u.get("theme")), parse_mode="HTML",
    )


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u or not u.get("is_vip"):
        return await update.message.reply_text(
            "🚫 /skip is a VIP feature. Use /buy to unlock!",
        )
    if not u.get("partner"):
        return await update.message.reply_text("⚠️ Not in a chat.")
    await block_internal(context, uid)
    await try_match(context, uid)


async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    await update.message.reply_text(
        "🕐  ✨  <b>Timezone</b>  ✨  🕐\n\n"
        "Select your timezone so greetings match your local time:",
        reply_markup=get_timezone_kb(), parse_mode="HTML",
    )


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    await update.message.reply_text(
        "🌐  ✨  <b>Language</b>  ✨  🌐\n\nChoose your language:",
        reply_markup=get_language_kb(), parse_mode="HTML",
    )
    
async def cmd_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create an anonymous WebRTC voice room with current partner."""
    from services.voice_rooms import create_voice_room
    await create_voice_room(context, update.effective_user.id)