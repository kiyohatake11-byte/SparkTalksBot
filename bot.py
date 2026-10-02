import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import logging
from threading import Thread
from flask import Flask

from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat, MenuButtonCommands
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, MessageReactionHandler, PreCheckoutQueryHandler,
    filters,
)

from config import TOKEN, PORT, VIP_CHECK_INTERVAL, QUEUE_CLEANUP_INTERVAL, CLEANUP_USERS_INTERVAL
from state import admin_cache
from database import init_db, refresh_admin_cache
from handlers.commands import (
    cmd_start, cmd_next, cmd_end, cmd_profile, cmd_settings,
    cmd_buy, cmd_help, cmd_report, cmd_block
)
from handlers.admin import (
    cmd_addvip, cmd_removevip, cmd_ban, cmd_unban, cmd_userinfo,
    cmd_stats, cmd_broadcast, cmd_dm, cmd_forceend, cmd_banlist,
    cmd_setadmin, cmd_removeadmin
)
from handlers.callbacks import on_callback
from handlers.messages import relay_chat, on_reaction
from handlers.payments import precheckout, successful_payment
from services.vip import check_expired_vips
from services.cleanup import cleanup_stale_queue, cleanup_inactive_users
from services.matching import background_matcher

# ──────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("sparktalks")


async def post_init(application):
    await init_db()
    await refresh_admin_cache()

    normal_cmds = [
        BotCommand("start", "Dashboard"),
        BotCommand("next", "Find a partner"),
        BotCommand("end", "End chat"),
        BotCommand("profile", "Your profile"),
        BotCommand("settings", "Settings"),
        BotCommand("buy", "VIP Store"),
        BotCommand("report", "Report partner"),
        BotCommand("block", "Block partner"),
        BotCommand("help", "Help"),
    ]
    admin_cmds = normal_cmds + [
        BotCommand("addvip", "Grant VIP"),
        BotCommand("removevip", "Remove VIP"),
        BotCommand("ban", "Ban user"),
        BotCommand("unban", "Unban user"),
        BotCommand("userinfo", "User info"),
        BotCommand("stats", "Stats"),
        BotCommand("broadcast", "Broadcast"),
        BotCommand("dm", "DM user"),
        BotCommand("forceend", "Force end"),
        BotCommand("banlist", "Ban list"),
        BotCommand("setadmin", "Promote admin"),
        BotCommand("removeadmin", "Remove admin"),
    ]

    await application.bot.set_my_commands(normal_cmds, scope=BotCommandScopeDefault())
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    for admin_id in admin_cache:
        if admin_id:
            try:
                await application.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e:
                logger.error(f"Failed to set admin commands for {admin_id}: {e}")

    if application.job_queue:
        application.job_queue.run_repeating(check_expired_vips, interval=VIP_CHECK_INTERVAL, first=10)
        application.job_queue.run_repeating(cleanup_stale_queue, interval=QUEUE_CLEANUP_INTERVAL, first=60)
        application.job_queue.run_repeating(cleanup_inactive_users, interval=CLEANUP_USERS_INTERVAL, first=120)
        application.job_queue.run_repeating(background_matcher, interval=0.5, first=1)

    logger.info("SparkTalks bot initialized with 2s background matcher.")


async def error_handler(update: object, context):
    logger.error(f"Exception: {context.error}", exc_info=context.error)


def run_web():
    web = Flask("")

    @web.route("/")
    def home():
        return "SparkTalks is online."

    @web.route("/health")
    def health():
        return "OK", 200

    web.run(host="0.0.0.0", port=PORT)


def main():
    if not TOKEN:
        print("CRITICAL: TELEGRAM_BOT_TOKEN missing!")
        return

    Thread(target=run_web, daemon=True).start()
    logger.info(f"Web server on port {PORT}")

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # User commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("block", cmd_block))

    # Admin commands
    app.add_handler(CommandHandler("addvip", cmd_addvip))
    app.add_handler(CommandHandler("removevip", cmd_removevip))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("dm", cmd_dm))
    app.add_handler(CommandHandler("forceend", cmd_forceend))
    app.add_handler(CommandHandler("banlist", cmd_banlist))
    app.add_handler(CommandHandler("setadmin", cmd_setadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))

    # Other handlers
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageReactionHandler(on_reaction))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_chat))
    app.add_error_handler(error_handler)

    logger.info("SparkTalks online...")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
