import asyncio
import sys
import signal
import logging
from datetime import timedelta, time as dt_time, timezone
from threading import Thread

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from telegram import (
    Update, BotCommandScopeDefault,
    BotCommandScopeChat, MenuButtonCommands,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, MessageReactionHandler, PreCheckoutQueryHandler,
    ChatMemberHandler,
    filters,
)

from config import (
    TOKEN, PORT, VIP_CHECK_INTERVAL,
    QUEUE_CLEANUP_INTERVAL, CLEANUP_USERS_INTERVAL,
    SCHEDULED_BROADCAST_CHECK_INTERVAL, OWNER_ID,
)
from state import users, admin_cache, analytics
from database import (
    init_db, refresh_admin_cache, users_collection,
    load_user_from_db, flush_writes,
    analytics_history_collection,
)
from utils import utcnow
from bot_commands import get_user_commands, get_admin_commands

from handlers.commands import (
    cmd_start, cmd_next, cmd_end, cmd_profile, cmd_settings,
    cmd_buy, cmd_help, cmd_report, cmd_block, cmd_cancel,
    cmd_invite, cmd_myvip, cmd_theme, cmd_skip, cmd_timezone, cmd_language,
    cmd_voice, cmd_search_gender,
    cmd_stop, cmd_saved, cmd_history,
    cmd_privacy, cmd_terms, cmd_appeal, cmd_achievements,
    cmd_community,
)
from handlers.admin import (
    cmd_addvip, cmd_removevip, cmd_warn, cmd_warnings, cmd_unwarn,
    cmd_resetprofile, cmd_resetstats, cmd_whois, cmd_userinfo,
    cmd_ban, cmd_unban, cmd_kick, cmd_mute, cmd_unmute,
    cmd_mutelist,
    cmd_unmuteall,
    cmd_forceend,
    cmd_banlist, cmd_blocked, cmd_unblock, cmd_clearblocks,
    cmd_stats, cmd_topusers, cmd_recent, cmd_vip, cmd_waiting, cmd_chats,
    cmd_broadcast, cmd_broadcast_media, cmd_dm, cmd_notify,
    cmd_broadcaststatus, cmd_pausebroadcast, cmd_resumebroadcast,
    cmd_stopbroadcast, cmd_cancelbroadcast,
    cmd_setadmin, cmd_removeadmin, cmd_adminlist,
    cmd_health, cmd_clearchat, cmd_maintenance, cmd_clearcache, cmd_logs,
    cmd_schedule, cmd_scheduled_list, cmd_scheduled_cancel,
    cmd_verify, cmd_unverify,
    cmd_forcedelete,
    cmd_purge,
    cmd_getfileid,
    cmd_appeals, cmd_resolve_appeal,
    cmd_checkchannel,
    cmd_gifts,
    cmd_vipinfo,
    cmd_vipbackfill,
    cmd_invites,
    cmd_banword, cmd_muteword, cmd_removeword, cmd_wordlist,
)
from handlers.callbacks import on_callback
from handlers.messages import relay_chat, on_reaction, on_edited_message
from handlers.community_events import on_chat_member
from handlers.payments import precheckout, successful_payment
from services.vip import check_expired_vips
from services.cleanup import (
    cleanup_stale_queue, cleanup_inactive_users, cleanup_last_next,
    cleanup_image_hashes, cleanup_duplicate_text, cleanup_media_timeouts,
)
from services.matching import background_matcher
from services.scheduler import check_scheduled_broadcasts, load_scheduled_from_db
from services.gifts import ensure_indexes as ensure_gift_indexes


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# DAILY ANALYTICS RESET (NEW)
# ══════════════════════════════════════════════════════════════

DAILY_ANALYTICS_KEYS = [
    "matches_today", "reports_today", "violations_today",
    "mutes_today", "vip_purchases_today", "broadcasts_sent",
    "referrals_today", "voice_rooms_today", "reconnects_today",
    "saves_today", "match_wait_total", "match_wait_count",
]


async def reset_daily_analytics(context):
    """Reset daily counters at midnight UTC. Save yesterday's snapshot to DB."""
    # ═══ Step 1: Save yesterday's snapshot ═══
    if analytics_history_collection is not None:
        try:
            yesterday = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            snapshot = {k: analytics.get(k, 0) for k in DAILY_ANALYTICS_KEYS}
            await analytics_history_collection.update_one(
                {"date": yesterday},
                {"$set": {
                    "date": yesterday,
                    "snapshot": snapshot,
                    "saved_at": utcnow(),
                }},
                upsert=True,
            )
            logger.info(f"📊 Analytics snapshot saved for {yesterday}")
        except Exception as e:
            logger.error(f"Analytics history save failed: {e}")

    # ═══ Step 2: Reset counters ═══
    old = {k: analytics.get(k, 0) for k in DAILY_ANALYTICS_KEYS}
    for k in DAILY_ANALYTICS_KEYS:
        analytics[k] = 0
    logger.info(f"📊 Daily analytics reset. Yesterday: {old}")


async def preload_active_users():
    if users_collection is None:
        logger.warning("⚠️ Preload skipped: DB not connected")
        return

    cutoff = utcnow() - timedelta(hours=6)
    count = failed = skipped = 0

    async def _do_preload():
        nonlocal count, failed, skipped
        try:
            cursor = users_collection.find(
                {"last_active": {"$gte": cutoff}, "is_banned": False},
                {"user_id": 1}
            ).sort("last_active", -1).limit(500)
            async for doc in cursor:
                uid = doc["user_id"]
                if uid in users:
                    skipped += 1
                    continue
                try:
                    loaded = await load_user_from_db(uid)
                    if loaded:
                        users[uid] = loaded
                        count += 1
                except Exception as e:
                    failed += 1
                    logger.debug(f"Preload skip {uid}: {e}")
            logger.info(f"✅ Preloaded {count} users (skipped: {skipped}, failed: {failed})")
        except Exception as e:
            logger.error(f"❌ Preload error: {e}", exc_info=True)

    task = asyncio.create_task(_do_preload())
    task.add_done_callback(
        lambda t: t.exception() and logger.error(f"Preload task failed: {t.exception()}")
    )


async def post_init(application):
    await init_db()
    await refresh_admin_cache()
    await preload_active_users()

    try:
        await ensure_gift_indexes()
    except Exception as e:
        logger.error(f"Gift index failed: {e}")

    try:
        await load_scheduled_from_db()
    except Exception as e:
        logger.error(f"Scheduled load failed: {e}")

    # ⭐ Load bad words filter
    try:
        from database import load_bad_words
        bw_count = await load_bad_words()
        logger.info(f"✅ Loaded {bw_count} bad word rules")
    except Exception as e:
        logger.error(f"Bad words load failed: {e}")

    # ⭐ Broadcast state recovery — reset stale state from crash
    try:
        from state import broadcast_state
        if broadcast_state.get("running"):
            logger.warning(
                "⚠️ Stale broadcast state detected (bot likely crashed mid-broadcast). Resetting."
            )
            broadcast_state.update({
                "running": False,
                "stop_requested": False,
                "paused": False,
                "cancel_requested": False,
                "type": None,
                "started_at": None,
                "processed": 0,
                "total": 0,
                "sent": 0,
                "failed": 0,
                "admin_uid": None,
            })
    except Exception as e:
        logger.error(f"Broadcast state recovery failed: {e}")

    analytics["start_time"] = utcnow()

    normal_cmds = get_user_commands()
    admin_cmds = get_admin_commands()

    await application.bot.set_my_commands(normal_cmds, scope=BotCommandScopeDefault())
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    for admin_id in admin_cache:
        if admin_id:
            try:
                await application.bot.set_my_commands(
                    admin_cmds, scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception as e:
                logger.error(f"Failed admin cmds for {admin_id}: {e}")

    if application.job_queue:
        application.job_queue.run_repeating(
            check_expired_vips, interval=VIP_CHECK_INTERVAL, first=10
        )
        application.job_queue.run_repeating(
            cleanup_stale_queue, interval=QUEUE_CLEANUP_INTERVAL, first=60
        )
        application.job_queue.run_repeating(
            cleanup_inactive_users, interval=CLEANUP_USERS_INTERVAL, first=120
        )
        application.job_queue.run_repeating(
            cleanup_last_next, interval=1800, first=300
        )
        application.job_queue.run_repeating(
            cleanup_image_hashes, interval=1800, first=300
        )
        application.job_queue.run_repeating(
            cleanup_duplicate_text, interval=1800, first=300
        )
        application.job_queue.run_repeating(
            cleanup_media_timeouts, interval=30, first=30
        )
        application.job_queue.run_repeating(
            background_matcher, interval=0.3, first=0.1
        )
        application.job_queue.run_repeating(
            flush_writes, interval=5, first=10
        )
        application.job_queue.run_repeating(
            check_scheduled_broadcasts,
            interval=SCHEDULED_BROADCAST_CHECK_INTERVAL,
            first=30,
        )
        # ⭐ Daily analytics reset at 00:00 UTC
        try:
            application.job_queue.run_daily(
                reset_daily_analytics,
                time=dt_time(hour=0, minute=0, second=0, tzinfo=timezone.utc),
                name="daily_analytics_reset",
            )
            logger.info("✅ Daily analytics reset scheduled at 00:00 UTC")
        except Exception as e:
            logger.error(f"Daily analytics reset schedule failed: {e}")

    try:
        loop = asyncio.get_running_loop()

        def _handle_sigterm():
            logger.warning("🛑 SIGTERM — flushing pending writes...")
            asyncio.create_task(flush_writes())

        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
        logger.info("✅ SIGTERM handler installed")
    except (NotImplementedError, AttributeError):
        logger.info("ℹ️ SIGTERM not supported on this platform")

    logger.info("🚀 SparkTalks initialized")


async def post_shutdown(application):
    try:
        if users_collection is not None:
            await asyncio.wait_for(flush_writes(), timeout=30)
            logger.info("💾 Final flush complete")
    except asyncio.TimeoutError:
        logger.warning("⚠️ Shutdown flush timed out")
    except Exception as e:
        logger.error(f"❌ Shutdown flush failed: {e}", exc_info=True)


async def error_handler(update: object, context):
    err = context.error
    logger.error(f"Exception: {err}", exc_info=err)

    try:
        if isinstance(update, Update):
            if update.callback_query:
                try:
                    await update.callback_query.answer(
                        "⚠️ Something went wrong. Try again.", show_alert=True,
                    )
                except Exception:
                    pass
            elif update.effective_message and update.effective_chat:
                if update.effective_chat.type == "private":
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⚠️  ✨  <b>Oops</b>  ✨  ⚠️\n"
                             "▎\n▎ Something went wrong.\n"
                             "▎\n💡 <i>Try /help or /start again.</i>",
                        parse_mode="HTML",
                    )
    except Exception:
        pass

    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🚨 <b>Bot Error</b>\n<code>{str(err)[:500]}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass


def run_web():
    from web.dashboard import create_dashboard_app
    from database import mongo_client

    app, socketio = create_dashboard_app(mongo_client, users, admin_cache, analytics)

    @app.route("/")
    def home():
        return "SparkTalks is online."

    @app.route("/health")
    def health():
        try:
            if mongo_client is None:
                return "DB_DOWN", 503
            if mongo_client.topology_description is None:
                return "DB_INIT", 503
            return "OK", 200
        except Exception:
            return "DEGRADED", 503

    socketio.run(
        app,
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        log_output=False,
        allow_unsafe_werkzeug=True,
    )


def main():
    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN missing!")
        return

    Thread(target=run_web, daemon=True).start()
    logger.info(f"Web server on port {PORT}")

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ═══ USER COMMANDS ═══
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("block", cmd_block))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("myvip", cmd_myvip))
    app.add_handler(CommandHandler("saved", cmd_saved))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("achievements", cmd_achievements))
    app.add_handler(CommandHandler("community", cmd_community))
    app.add_handler(CommandHandler("theme", cmd_theme))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("search_gender", cmd_search_gender))
    app.add_handler(CommandHandler("appeal", cmd_appeal))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("terms", cmd_terms))

    # ═══ ADMIN — USER MGMT ═══
    app.add_handler(CommandHandler("addvip", cmd_addvip))
    app.add_handler(CommandHandler("removevip", cmd_removevip))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("warnings", cmd_warnings))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("resetprofile", cmd_resetprofile))
    app.add_handler(CommandHandler("resetstats", cmd_resetstats))
    app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    app.add_handler(CommandHandler("whois", cmd_whois))
    app.add_handler(CommandHandler("verify", cmd_verify))
    app.add_handler(CommandHandler("unverify", cmd_unverify))

    # ═══ ADMIN — MODERATION ═══
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("mutelist", cmd_mutelist))
    app.add_handler(CommandHandler("unmuteall", cmd_unmuteall))
    app.add_handler(CommandHandler("forceend", cmd_forceend))
    app.add_handler(CommandHandler("banlist", cmd_banlist))
    app.add_handler(CommandHandler("blocked", cmd_blocked))
    app.add_handler(CommandHandler("unblock", cmd_unblock))
    app.add_handler(CommandHandler("clearblocks", cmd_clearblocks))

    # ═══ ADMIN — ANALYTICS ═══
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("topusers", cmd_topusers))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("vip", cmd_vip))
    app.add_handler(CommandHandler("waiting", cmd_waiting))
    app.add_handler(CommandHandler("chats", cmd_chats))

    # ═══ ADMIN — COMMUNICATION ═══
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("broadcast_media", cmd_broadcast_media))
    app.add_handler(CommandHandler("broadcaststatus", cmd_broadcaststatus))
    app.add_handler(CommandHandler("pausebroadcast", cmd_pausebroadcast))
    app.add_handler(CommandHandler("resumebroadcast", cmd_resumebroadcast))
    app.add_handler(CommandHandler("stopbroadcast", cmd_stopbroadcast))
    app.add_handler(CommandHandler("cancelbroadcast", cmd_cancelbroadcast))
    app.add_handler(CommandHandler("dm", cmd_dm))
    app.add_handler(CommandHandler("notify", cmd_notify))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("scheduled", cmd_scheduled_list))
    app.add_handler(CommandHandler("unschedule", cmd_scheduled_cancel))

    # ═══ ADMIN — ADMIN MGMT ═══
    app.add_handler(CommandHandler("setadmin", cmd_setadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))
    app.add_handler(CommandHandler("adminlist", cmd_adminlist))

    # ═══ ADMIN — SYSTEM ═══
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("clearchat", cmd_clearchat))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("clearcache", cmd_clearcache))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("forcedelete", cmd_forcedelete))
    app.add_handler(CommandHandler("purge", cmd_purge))
    app.add_handler(CommandHandler("getfileid", cmd_getfileid))
    app.add_handler(CommandHandler("appeals", cmd_appeals))
    app.add_handler(CommandHandler("resolve", cmd_resolve_appeal))
    app.add_handler(CommandHandler("checkchannel", cmd_checkchannel))
    app.add_handler(CommandHandler("gifts", cmd_gifts))
    app.add_handler(CommandHandler("vipinfo", cmd_vipinfo))
    app.add_handler(CommandHandler("vipbackfill", cmd_vipbackfill))
    app.add_handler(CommandHandler("invites", cmd_invites))

    # ═══ ADMIN — BAD WORDS ═══
    app.add_handler(CommandHandler("banword", cmd_banword))
    app.add_handler(CommandHandler("muteword", cmd_muteword))
    app.add_handler(CommandHandler("removeword", cmd_removeword))
    app.add_handler(CommandHandler("wordlist", cmd_wordlist))

    # ═══ OTHER HANDLERS ═══
    app.add_handler(ChatMemberHandler(
        on_chat_member, ChatMemberHandler.CHAT_MEMBER
    ))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageReactionHandler(on_reaction))
    app.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.TEXT, on_edited_message
    ))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_chat))
    app.add_error_handler(error_handler)

    logger.info("SparkTalks online... 🚀")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()