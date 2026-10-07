import asyncio
import sys
import signal

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging
from threading import Thread

from telegram import (
    Update, BotCommand, BotCommandScopeDefault,
    BotCommandScopeChat, MenuButtonCommands,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, MessageReactionHandler, PreCheckoutQueryHandler,
    filters,
)

from config import (
    TOKEN, PORT, VIP_CHECK_INTERVAL,
    QUEUE_CLEANUP_INTERVAL, CLEANUP_USERS_INTERVAL,
    SCHEDULED_BROADCAST_CHECK_INTERVAL,
)
from state import users, admin_cache, analytics
from database import (
    init_db, refresh_admin_cache, users_collection,
    load_user_from_db, flush_writes,
)
from utils import utcnow
from datetime import timedelta

from handlers.commands import (
    cmd_start, cmd_next, cmd_end, cmd_profile, cmd_settings,
    cmd_buy, cmd_help, cmd_report, cmd_block, cmd_cancel,
    cmd_invite, cmd_myvip, cmd_theme, cmd_skip, cmd_timezone, cmd_language,
    cmd_voice,
)
from handlers.admin import (
    cmd_addvip, cmd_removevip, cmd_warn, cmd_warnings, cmd_unwarn,
    cmd_resetprofile, cmd_resetstats, cmd_whois, cmd_userinfo,
    cmd_ban, cmd_unban, cmd_kick, cmd_mute, cmd_unmute, cmd_forceend,
    cmd_banlist, cmd_blocked, cmd_unblock, cmd_clearblocks,
    cmd_stats, cmd_topusers, cmd_recent, cmd_vip, cmd_waiting, cmd_chats,
    cmd_broadcast, cmd_broadcast_media, cmd_dm, cmd_notify,
    cmd_setadmin, cmd_removeadmin, cmd_adminlist,
    cmd_health, cmd_clearchat, cmd_maintenance, cmd_clearcache, cmd_logs,
    cmd_schedule, cmd_scheduled_list, cmd_scheduled_cancel,
    cmd_verify, cmd_unverify,
)
from handlers.callbacks import on_callback
from handlers.messages import relay_chat, on_reaction, on_edited_message
from handlers.payments import precheckout, successful_payment
from services.vip import check_expired_vips
from services.cleanup import (
    cleanup_stale_queue, cleanup_inactive_users, cleanup_last_next,
    cleanup_image_hashes,
)
from services.matching import background_matcher
from services.scheduler import check_scheduled_broadcasts


# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# PRELOAD
# ══════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════
# POST INIT
# ══════════════════════════════════════════════════════════════

async def post_init(application):
    await init_db()
    await refresh_admin_cache()
    await preload_active_users()
    analytics["start_time"] = utcnow()

    normal_cmds = [
        BotCommand("start", "Dashboard"),
        BotCommand("next", "Find a partner"),
        BotCommand("end", "End chat"),
        BotCommand("cancel", "Cancel current action"),
        BotCommand("profile", "Your profile"),
        BotCommand("settings", "Settings"),
        BotCommand("buy", "VIP Store"),
        BotCommand("invite", "Invite & earn VIP"),
        BotCommand("myvip", "Your VIP status"),
        BotCommand("theme", "Chat theme"),
        BotCommand("timezone", "Set your timezone"),
        BotCommand("language", "Change language"),
        BotCommand("voice", "Anonymous voice room"),
        BotCommand("report", "Report partner"),
        BotCommand("block", "Block partner (VIP only)"),
        BotCommand("skip", "Skip & block (VIP)"),
        BotCommand("help", "Help"),
    ]

    admin_cmds = normal_cmds + [
        BotCommand("addvip", "Grant VIP"),
        BotCommand("removevip", "Remove VIP"),
        BotCommand("warn", "Warn a user"),
        BotCommand("warnings", "View warnings"),
        BotCommand("unwarn", "Remove warning"),
        BotCommand("resetprofile", "Reset profile"),
        BotCommand("resetstats", "Reset stats"),
        BotCommand("userinfo", "Detailed user info"),
        BotCommand("whois", "Quick user lookup"),
        BotCommand("verify", "Verify user"),
        BotCommand("unverify", "Unverify user"),
        BotCommand("ban", "Ban user"),
        BotCommand("unban", "Unban user"),
        BotCommand("kick", "Disconnect from chat"),
        BotCommand("mute", "Mute user"),
        BotCommand("unmute", "Unmute user"),
        BotCommand("forceend", "Force end chat"),
        BotCommand("banlist", "List banned users"),
        BotCommand("blocked", "View blocks"),
        BotCommand("unblock", "Remove block"),
        BotCommand("clearblocks", "Clear all blocks"),
        BotCommand("stats", "Bot stats"),
        BotCommand("topusers", "Top users"),
        BotCommand("recent", "Recent users"),
        BotCommand("vip", "VIP users list"),
        BotCommand("waiting", "Queue status"),
        BotCommand("chats", "Active chats"),
        BotCommand("broadcast", "Broadcast text"),
        BotCommand("broadcast_media", "Broadcast media"),
        BotCommand("schedule", "Schedule broadcast"),
        BotCommand("scheduled", "List scheduled"),
        BotCommand("unschedule", "Cancel scheduled"),
        BotCommand("dm", "DM a user"),
        BotCommand("notify", "Silent notify"),
        BotCommand("setadmin", "Promote admin"),
        BotCommand("removeadmin", "Remove admin"),
        BotCommand("adminlist", "List admins"),
        BotCommand("health", "System health"),
        BotCommand("clearchat", "Clear user state"),
        BotCommand("maintenance", "Toggle maintenance"),
        BotCommand("clearcache", "Clear cache"),
        BotCommand("logs", "Admin action logs"),
    ]

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

    # Background jobs
    if application.job_queue:
        application.job_queue.run_repeating(check_expired_vips, interval=VIP_CHECK_INTERVAL, first=10)
        application.job_queue.run_repeating(cleanup_stale_queue, interval=QUEUE_CLEANUP_INTERVAL, first=60)
        application.job_queue.run_repeating(cleanup_inactive_users, interval=CLEANUP_USERS_INTERVAL, first=120)
        application.job_queue.run_repeating(cleanup_last_next, interval=1800, first=300)
        application.job_queue.run_repeating(cleanup_image_hashes, interval=1800, first=300)
        application.job_queue.run_repeating(background_matcher, interval=0.3, first=0.5)
        application.job_queue.run_repeating(flush_writes, interval=5, first=10)
        application.job_queue.run_repeating(
            check_scheduled_broadcasts,
            interval=SCHEDULED_BROADCAST_CHECK_INTERVAL,
            first=30,
        )

    # SIGTERM
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
    logger.error(f"Exception: {context.error}", exc_info=context.error)


# ══════════════════════════════════════════════════════════════
# WEB SERVER (Flask + Socket.IO for anonymous voice rooms)
# ══════════════════════════════════════════════════════════════

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

    # Socket.IO server (supports WebRTC signaling)
    socketio.run(
        app,
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        log_output=False,
        allow_unsafe_werkzeug=True,
    )


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

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

    # USER COMMANDS
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("block", cmd_block))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("myvip", cmd_myvip))
    app.add_handler(CommandHandler("theme", cmd_theme))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("voice", cmd_voice))

    # ADMIN — USER MGMT
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

    # ADMIN — MODERATION
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("forceend", cmd_forceend))
    app.add_handler(CommandHandler("banlist", cmd_banlist))
    app.add_handler(CommandHandler("blocked", cmd_blocked))
    app.add_handler(CommandHandler("unblock", cmd_unblock))
    app.add_handler(CommandHandler("clearblocks", cmd_clearblocks))

    # ADMIN — ANALYTICS
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("topusers", cmd_topusers))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("vip", cmd_vip))
    app.add_handler(CommandHandler("waiting", cmd_waiting))
    app.add_handler(CommandHandler("chats", cmd_chats))

    # ADMIN — COMMUNICATION
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("broadcast_media", cmd_broadcast_media))
    app.add_handler(CommandHandler("dm", cmd_dm))
    app.add_handler(CommandHandler("notify", cmd_notify))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("scheduled", cmd_scheduled_list))
    app.add_handler(CommandHandler("unschedule", cmd_scheduled_cancel))

    # ADMIN — ADMIN MGMT
    app.add_handler(CommandHandler("setadmin", cmd_setadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))
    app.add_handler(CommandHandler("adminlist", cmd_adminlist))

    # ADMIN — SYSTEM
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("clearchat", cmd_clearchat))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("clearcache", cmd_clearcache))
    app.add_handler(CommandHandler("logs", cmd_logs))

    # OTHER
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