import logging
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove,
    LinkPreviewOptions,
)
from telegram.ext import ContextTypes

from config import (
    TIMEZONE_OPTIONS, SUPPORTED_LANGUAGES,
    OWNER_ID, BOT_USERNAME,
    WELCOME_IMAGE_FILE_ID, DASHBOARD_IMAGE_FILE_ID,
)
from state import users, analytics, queue, queue_set, queue_lock
from database import get_user, load_user_from_db, save_user_to_db, create_new_user
from utils import (
    box_card, box_simple, utcnow, user_local_time, safe_send,
    esc,
)
from keyboards import (
    get_main_keyboard, get_store_markup, get_profile_text,
    get_settings_text, get_settings_main_kb,
    get_language_kb, get_timezone_kb, get_theme_kb,
)
from services.matching import (
    try_match, end_chat_internal, report_internal, block_internal,
    cancel_search, disconnect,
)

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# TIME-BASED GREETING
# ══════════════════════════════════════════════════════════════

def _get_time_greeting(u: dict = None) -> dict:
    offset = (u or {}).get("timezone_offset", 5.5)
    if not isinstance(offset, (int, float)):
        try:
            offset = float(offset)
        except (ValueError, TypeError):
            offset = 5.5
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
    safe_name = esc(name)

    if u.get("is_vip"):
        tier = u.get("vip_tier_name") or "VIP"
        status = f"👑 {esc(tier)}"
    else:
        status = "⚪ Free Member"

    title = (
        f"{tg['title_left']}  {tg['title_mid']}  "
        f"<b>{tg['greeting']}, {safe_name}!</b>  "
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
    safe_name = esc(name)
    title = f"{tg['title_left']}  ✨  <b>Welcome to SparkTalks</b>  ✨  {tg['title_right']}"
    return (
        f"{title}\n"
        "▎\n"
        "▎ 💬 <i>Chat anonymously with strangers instantly!</i>\n"
        "▎\n"
        f"▎ {tg['emoji']} Hey <b>{safe_name}</b>, welcome aboard!\n"
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

    try:
        if uid not in users:
            db_user = await load_user_from_db(uid)
            if db_user:
                users[uid] = db_user
            else:
                users[uid] = await create_new_user(uid, user.first_name, user.username)

        users[uid]["name"] = user.first_name
        users[uid]["username"] = user.username
        u = users[uid]

        # ⭐ FIX: reset any pending interactive input on /start
        u["awaiting_input"] = None
        u["_pending_save_partner_id"] = None

        tz = u.get("timezone_offset", 5.5)
        if not isinstance(tz, (int, float)):
            try:
                tz = float(tz)
            except (ValueError, TypeError):
                tz = 5.5
            u["timezone_offset"] = tz
            logger.warning(f"Fixed timezone_offset for {uid}: {tz}")

        ved = u.get("vip_expiry_date")
        if ved is not None and not isinstance(ved, datetime):
            logger.warning(f"Invalid vip_expiry_date for {uid}: {ved!r} — clearing")
            u["vip_expiry_date"] = None
            u["is_vip"] = False
            u["vip_tier_name"] = "None"

        partner = u.get("partner")
        if partner is not None and not isinstance(partner, int):
            try:
                u["partner"] = int(partner)
            except (ValueError, TypeError):
                u["partner"] = None
                u["state"] = "IDLE"
                logger.warning(f"Invalid partner for {uid} — cleared")

        jd = u.get("joined_date")
        if jd is not None and not isinstance(jd, datetime):
            u["joined_date"] = utcnow()

        g = u.get("gender")
        if g is not None and g not in ("Male", "Female"):
            logger.warning(f"Invalid gender for {uid}: {g!r} — clearing")
            u["gender"] = None

    except Exception as e:
        logger.error(f"cmd_start load error for {uid}: {e}", exc_info=True)
        try:
            from database import users_collection
            if users_collection is not None:
                await users_collection.delete_one({"user_id": uid})
            from state import deleted_user_ids
            deleted_user_ids.add(uid)
            users.pop(uid, None)
            deleted_user_ids.discard(uid)
        except Exception as e2:
            logger.error(f"Emergency reset failed for {uid}: {e2}")

        return await update.message.reply_text(
            "⚠️  ✨  <b>Setup Error</b>  ✨  ⚠️\n"
            "▎\n"
            "▎ We detected a corrupted profile.\n"
            "▎ Your data has been reset.\n"
            "▎\n"
            "💡 <i>Please send /start again to begin fresh.</i>",
            parse_mode="HTML",
        )

    if u.get("is_banned"):
        return await update.message.reply_text(
            "🚫  ✨  <b>Access Denied</b>  ✨  🚫\n"
            "▎\n"
            "▎ You have been banned.\n"
            "▎\n"
            "💡 <i>Send /appeal to contest this.</i>",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove(),
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"

    if not u.get("timezone_set_manually"):
        country = u.get("country") or ""
        if not isinstance(country, str):
            country = ""
        if "India" in country: u["timezone_offset"] = 5.5
        elif "Pakistan" in country: u["timezone_offset"] = 5.0
        elif "USA" in country: u["timezone_offset"] = -5.0
        elif "UK" in country: u["timezone_offset"] = 0.0
        elif "Canada" in country: u["timezone_offset"] = -5.0
        elif "UAE" in country or "Gulf" in country: u["timezone_offset"] = 4.0
        elif "Nepal" in country: u["timezone_offset"] = 5.75
        elif "Moscow" in country: u["timezone_offset"] = 3.0

    await save_user_to_db(uid, u)

    if context.args:
        try:
            payload = context.args[0].lower().strip()
            logger.info(f"Deep link: {payload!r}")

            if payload.startswith("ref_"):
                from services.referral import handle_referral_payload
                applied = await handle_referral_payload(context, uid, payload)
                if applied:
                    await update.message.reply_text(
                        "🎁 Referral applied! Welcome to SparkTalks.",
                        parse_mode="HTML",
                    )

            if payload in ("vip", "buy", "store"):
                text, kb_markup = get_store_markup(u)
                return await update.message.reply_text(
                    text, reply_markup=kb_markup, parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

            if payload == "settings" and u.get("gender"):
                return await update.message.reply_text(
                    get_settings_text(u),
                    reply_markup=get_settings_main_kb(u),
                    parse_mode="HTML",
                )

            if payload == "help":
                return await cmd_help(update, context)
        except Exception as e:
            logger.error(f"Deep link error for {uid}: {e}", exc_info=True)

    if u.get("partner"):
        try:
            await disconnect(context, uid, u["partner"], ender_id=uid)
        except Exception as e:
            logger.warning(f"Disconnect during /start failed for {uid}: {e}")
            u["partner"] = None
            u["state"] = "IDLE"

    try:
        force_onboarding = bool(u.get("needs_onboarding")) or not u.get("gender")

        if not force_onboarding:
            dashboard_card = _build_dashboard(u, name)

            if DASHBOARD_IMAGE_FILE_ID:
                try:
                    await update.message.reply_photo(
                        photo=DASHBOARD_IMAGE_FILE_ID,
                        caption=dashboard_card,
                        reply_markup=get_main_keyboard(),
                        parse_mode="HTML",
                    )
                    return
                except Exception as e:
                    logger.warning(
                        f"Dashboard banner failed for {uid}: {e} — "
                        f"falling back to text"
                    )

            await update.message.reply_text(
                dashboard_card,
                reply_markup=get_main_keyboard(),
                parse_mode="HTML",
            )
            return

        kb_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
            InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
        ]])
        onboarding_text = _build_onboarding(name, u)

        if WELCOME_IMAGE_FILE_ID:
            try:
                await update.message.reply_photo(
                    photo=WELCOME_IMAGE_FILE_ID,
                    caption=onboarding_text,
                    reply_markup=kb_markup,
                    parse_mode="HTML",
                )
                return
            except Exception as e:
                logger.warning(
                    f"Welcome banner failed for {uid}: {e} — "
                    f"falling back to text"
                )

        await update.message.reply_text(
            onboarding_text,
            reply_markup=kb_markup,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"cmd_start render error for {uid}: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️  ✨  <b>Display Error</b>  ✨  ⚠️\n"
            "▎\n"
            "▎ Something went wrong.\n"
            "▎\n"
            "💡 <i>Try /settings or /help.</i>",
            parse_mode="HTML",
        )


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await try_match(context, update.effective_user.id)


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await end_chat_internal(context, update.effective_user.id)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return

    async with queue_lock:
        try:
            queue.remove(uid)
        except ValueError:
            pass
        queue_set.discard(uid)

    if u.get("partner"):
        await disconnect(context, uid, u["partner"], ender_id=uid)
    else:
        u["state"] = "IDLE"

    body = (
        "🛑  ✨  <b>Stopped</b>  ✨  🛑\n"
        "▎\n"
        "▎ ✅ Everything cleared.\n"
        "▎\n"
        "▎ 🎯 <b>You're free</b>\n"
        "▎   ├ 🎲 /next — Start fresh\n"
        "▎   ├ 🏠 /start — Dashboard\n"
        "▎   └ ❓ /help — Commands\n"
        "▎\n"
        "💡 <i>Ready when you are!</i>"
    )
    await update.message.reply_text(
        body, reply_markup=get_main_keyboard(), parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    if u.get("awaiting_input"):
        u["awaiting_input"] = None
        u["_pending_save_partner_id"] = None
        await save_user_to_db(uid, u)
        return await update.message.reply_text(
            "✅  ✨  <b>Cancelled</b>  ✨  ✅\n"
            "▎\n▎ Action cancelled.\n▎\n💡 <i>Ready to continue!</i>",
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    if u.get("state") == "SEARCHING":
        return await cancel_search(context, uid)
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
    text, kb_markup = get_store_markup(u)
    await update.message.reply_text(
        text, reply_markup=kb_markup, parse_mode="HTML",
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
        "▎   ├ 🚨 /stop   — Emergency stop\n"
        "▎   └ ❌ /cancel — Cancel action\n"
        "▎\n"
        "▎ 👤  <b>Profile</b>\n"
        "▎   ├ 👤 /profile      — Your profile\n"
        "▎   ├ ⚙️ /settings     — Settings\n"
        "▎   ├ 🎨 /theme        — Chat theme\n"
        "▎   ├ 🕐 /timezone     — Set timezone\n"
        "▎   ├ 🌐 /language     — Change language\n"
        "▎   └ 🛍️ /buy          — VIP Store\n"
        "▎\n"
        "▎ 🎁  <b>Rewards</b>\n"
        "▎   ├ 🎁 /invite       — Invite (20 = 1 day VIP)\n"
        "▎   ├ ⭐ /myvip        — Your VIP status\n"
        "▎   ├ 💾 /saved        — Saved partners (VIP)\n"
        "▎   ├ 🔄 /skip         — Skip & block (VIP)\n"
        "▎   ├ 🎙️ /voice        — Voice call (VIP)\n"
        "▎   ├ 📜 /history      — Chat history\n"
        "▎   ├ 🏆 /achievements — Your badges\n"
        "▎   └ 📢 /community   — Join our channel\n"
        "▎\n"
        "▎ 🛡️  <b>Safety</b>\n"
        "▎   ├ 🚨 /report — Report partner\n"
        "▎   ├ 🚫 /block  — Block (VIP)\n"
        "▎   ├ 🔒 /privacy — Privacy\n"
        "▎   ├ 📜 /terms   — Terms\n"
        "▎   └ ⚖️ /appeal  — Appeal a ban\n"
        "▎\n"
        "▎ 👑  <b>VIP Perks</b>\n"
        "▎   ├ 🚻 Gender filter\n"
        "▎   ├ 🎙️ Voice rooms\n"
        "▎   ├ 🔄 Reconnect\n"
        "▎   ├ 💾 Save Partner\n"
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
# REWARDS / VIP
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

    saved = u.get("saved_partner_ids") or []
    reconnects_today = 0
    try:
        from services.reconnect import _get_reconnect_count
        reconnects_today = _get_reconnect_count(uid)
    except Exception:
        pass

    lines = [
        "⭐  ✨  <b>Your VIP Status</b>  ✨  ⭐",
        "▎",
        f"▎ Status : {status}",
        f"▎ Expires : {exp_str}",
        "▎",
        "▎ 🎁  <b>Usage</b>",
        f"▎   ├ 💾 Saved partners : {len(saved)}/20",
        f"▎   └ 🔄 Reconnects today : {reconnects_today}/5",
    ]
    if history:
        lines.append("▎")
        lines.append("▎ 🧾  <b>Recent Purchases</b>")
        for h in reversed(history[-5:]):
            lines.append(f"▎   • {h.get('tier', 'VIP')} — {h.get('at', '')[:10]}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    if not u.get("is_vip"):
        kb_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
        ]])
        return await update.message.reply_text(
            "🔒  ✨  <b>VIP Feature</b>  ✨  🔒\n"
            "▎\n"
            "▎ ⚠️ Save Partner is a VIP feature.\n"
            "▎\n"
            "💡 Upgrade to save & reconnect with partners!",
            reply_markup=kb_markup, parse_mode="HTML",
        )
    from services.saved_partners import list_saved_partners
    await list_saved_partners(context, uid)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    fb = u.get("match_feedback") or []
    good = sum(1 for x in fb if x.get("fb") == "FB_GOOD")
    bad = sum(1 for x in fb if x.get("fb") == "FB_BAD")
    total = len(fb)
    score = int((good / total) * 100) if total else 0

    lines = [
        "📜  ✨  <b>Your History</b>  ✨  📜",
        "▎",
        "▎ 📊  <b>Overall</b>",
        f"▎   ├ 💬 Chats : {u.get('total_chats', 0)}",
        f"▎   ├ 🏆 Matches : {u.get('total_matches', 0)}",
        f"▎   └ 🚨 Reports : {u.get('report_count', 0)}",
        "▎",
        "▎ 👍  <b>Match Feedback</b>",
        f"▎   ├ ✅ Good : {good}",
        f"▎   ├ ❌ Bad : {bad}",
        f"▎   └ 📈 Score : {score}%",
    ]
    if fb:
        lines.append("▎")
        lines.append("▎ 🕐  <b>Recent</b>")
        for entry in reversed(fb[-5:]):
            emoji = "✅" if entry.get("fb") == "FB_GOOD" else "❌"
            at = str(entry.get("at", ""))[:16]
            lines.append(f"▎   {emoji} {at}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return
    from services.achievements import ACHIEVEMENTS, get_unlocked
    unlocked = set(u.get("achievements") or [])
    lines = [
        "🏆  ✨  <b>Achievements</b>  ✨  🏆",
        "▎",
        f"▎ Unlocked : <b>{len(unlocked)}/{len(ACHIEVEMENTS)}</b>",
        "▎",
    ]
    for key, meta in ACHIEVEMENTS.items():
        icon = "✅" if key in unlocked else "🔒"
        lines.append(f"▎ {icon} {meta['emoji']} <b>{meta['name']}</b>")
        lines.append(f"▎    <i>{meta['desc']}</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_community(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    if not u:
        return

    from services.community import is_configured, prompt_join, check_membership

    if not is_configured():
        return await update.message.reply_text(
            "ℹ️ Community channel is not configured yet.",
        )

    if u.get("joined_community"):
        return await update.message.reply_text(
            "✅  ✨  <b>You're already in!</b>  ✨  ✅\n"
            "▎\n"
            "▎ 🎉 Thanks for being part of our community.\n"
            "▎\n"
            "💡 <i>Use /next to find a partner!</i>",
            parse_mode="HTML",
        )

    if await check_membership(context, uid):
        u["joined_community"] = True
        await save_user_to_db(uid, u)
        return await update.message.reply_text(
            "✅ Verified! You can match now. Use /next 🎲",
            parse_mode="HTML",
        )

    await prompt_join(context, uid)


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
    from services.voice_rooms import create_voice_room
    await create_voice_room(context, update.effective_user.id)


# ══════════════════════════════════════════════════════════════
# PRIVACY / TERMS / APPEAL
# ══════════════════════════════════════════════════════════════

async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒  ✨  <b>Privacy Policy</b>  ✨  🔒\n"
        "▎\n"
        "▎ 📋 <b>What we store</b>\n"
        "▎   ├ Your Telegram ID\n"
        "▎   ├ Profile (gender, age, bio)\n"
        "▎   ├ Chat stats\n"
        "▎   └ VIP purchase records\n"
        "▎\n"
        "▎ 🚫 <b>What we DON'T do</b>\n"
        "▎   ├ We don't read your messages\n"
        "▎   ├ We don't sell your data\n"
        "▎   └ We don't share with 3rd parties\n"
        "▎\n"
        "▎ 📧 <b>Data requests</b>\n"
        "▎   └ Contact owner for deletion\n"
        "▎\n"
        "💡 <i>Contact owner for queries.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📜  ✨  <b>Terms of Service</b>  ✨  📜\n"
        "▎\n"
        "▎ ✅ <b>Allowed</b>\n"
        "▎   ├ Respectful conversations\n"
        "▎   ├ Sharing interests\n"
        "▎   └ Reporting violations\n"
        "▎\n"
        "▎ 🚫 <b>Not Allowed</b>\n"
        "▎   ├ Spam or advertising\n"
        "▎   ├ Sharing links / contacts\n"
        "▎   ├ NSFW content\n"
        "▎   ├ Impersonation\n"
        "▎   └ Harassment\n"
        "▎\n"
        "▎ ⚠️ <b>Violations lead to</b>\n"
        "▎   ├ Warnings → Mute → Ban\n"
        "▎   └ Permanent for severe cases\n"
        "▎\n"
        "💡 <i>Use /report to report violations.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = users.get(uid)
    if not u:
        u = await get_user(uid)
    if not u:
        return
    if not u.get("is_banned"):
        return await update.message.reply_text(
            "✅ You are not banned.", parse_mode="HTML",
        )
    if not context.args:
        return await update.message.reply_text(
            "⚖️  ✨  <b>Appeal a Ban</b>  ✨  ⚖️\n"
            "▎\n"
            "▎ Usage: <code>/appeal your reason here</code>\n"
            "▎\n"
            "💡 <i>Be honest — appeals are reviewed manually.</i>",
            parse_mode="HTML",
        )
    reason = " ".join(context.args)[:500]
    if OWNER_ID:
        body = box_card("Ban Appeal", [
            {"type": "kv", "items": [
                ("👤 User", f"<code>{uid}</code>"),
                ("📛 Name", esc(u.get("name") or "—")),
                ("🚫 Ban reason", esc(u.get("banned_reason") or "—")),
            ]},
            {"type": "divider"},
            {"type": "text", "content": f"📝 <b>Appeal:</b>\n{esc(reason)}"},
            {"type": "divider"},
            {"type": "text", "content":
                f"➡️ <code>/unban {uid}</code> to approve"},
        ], emoji="⚖️")
        await safe_send(context, OWNER_ID, body, parse_mode="HTML")
    await update.message.reply_text(
        "✅  ✨  <b>Appeal Sent</b>  ✨  ✅\n"
        "▎\n"
        "▎ Your appeal has been forwarded to the owner.\n"
        "▎\n"
        "💡 <i>You'll be notified if approved.</i>",
        parse_mode="HTML",
    )