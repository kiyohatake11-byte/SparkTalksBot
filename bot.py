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
    AIORateLimiter,
)

from config import (
    TOKEN, PORT, VIP_CHECK_INTERVAL,
    QUEUE_CLEANUP_INTERVAL, CLEANUP_USERS_INTERVAL,
    SCHEDULED_BROADCAST_CHECK_INTERVAL, OWNER_ID,
    WEBHOOK_URL, WEBHOOK_SECRET,
)
from state import users, admin_cache, analytics
from database import (
    init_db, refresh_admin_cache, users_collection,
    load_user_from_db, flush_writes,
    analytics_history_collection,
)
from utils import utcnow, safe_send, log_event
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
    cmd_mutelist, cmd_unmuteall, cmd_forceend,
    cmd_banlist, cmd_blocked, cmd_unblock, cmd_clearblocks,
    cmd_endallchats,
    cmd_stats, cmd_topusers, cmd_recent, cmd_vip, cmd_waiting, cmd_chats,
    cmd_broadcast, cmd_broadcast_media, cmd_dm, cmd_notify,
    cmd_broadcaststatus, cmd_pausebroadcast, cmd_resumebroadcast,
    cmd_stopbroadcast, cmd_cancelbroadcast,
    cmd_setadmin, cmd_removeadmin, cmd_adminlist,
    cmd_health, cmd_clearchat, cmd_maintenance, cmd_clearcache, cmd_logs,
    cmd_schedule, cmd_scheduled_list, cmd_scheduled_cancel,
    cmd_verify, cmd_unverify,
    cmd_forcedelete, cmd_purge, cmd_getfileid,
    cmd_appeals, cmd_resolve_appeal, cmd_checkchannel,
    cmd_gifts, cmd_vipinfo, cmd_vipbackfill, cmd_invites,
    cmd_chatdb, cmd_debug_owner, cmd_reports, cmd_logtest,
    cmd_banword, cmd_muteword, cmd_removeword, cmd_wordlist,
    cmd_resetmatching,
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


# ══════════════════════════════════════════════════════════════
# ⭐ LOGGING — IMMEDIATE FLUSH
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

for _handler in logging.root.handlers:
    if hasattr(_handler, 'stream'):
        _original_emit = _handler.emit
        def _make_emitter(h, orig):
            def _emit(record):
                orig(record)
                try:
                    h.stream.flush()
                except Exception:
                    pass
            return _emit
        _handler.emit = _make_emitter(_handler, _original_emit)

logger = logging.getLogger("sparktalks")

logger.info("=" * 70)
logger.info("🔧🔧🔧 BOT.PY MODULE LOADED")
logger.info("=" * 70)


# ══════════════════════════════════════════════════════════════
# ⭐ IDEMPOTENT GUARD
# ══════════════════════════════════════════════════════════════
_POST_INIT_DONE = False


DAILY_ANALYTICS_KEYS = [
    "matches_today", "reports_today", "violations_today",
    "mutes_today", "vip_purchases_today", "broadcasts_sent",
    "referrals_today", "voice_rooms_today", "reconnects_today",
    "saves_today", "match_wait_total", "match_wait_count",
]


async def reset_daily_analytics(context):
    if analytics_history_collection is not None:
        try:
            yesterday = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            snapshot = {k: analytics.get(k, 0) for k in DAILY_ANALYTICS_KEYS}
            await analytics_history_collection.update_one(
                {"date": yesterday},
                {"$set": {"date": yesterday, "snapshot": snapshot, "saved_at": utcnow()}},
                upsert=True,
            )
        except Exception as e:
            logger.error(f"Analytics snapshot failed: {e}")

    old = {k: analytics.get(k, 0) for k in DAILY_ANALYTICS_KEYS}
    for k in DAILY_ANALYTICS_KEYS:
        analytics[k] = 0
    logger.info(f"📊 Daily analytics reset. Yesterday: {old}")


async def persist_active_chats(context=None):
    from database import save_user_to_db
    saved = 0
    for uid, u in list(users.items()):
        if u.get("partner") and u.get("state") == "CHAT":
            try:
                await save_user_to_db(uid, u)
                saved += 1
            except Exception as e:
                logger.debug(f"persist chat {uid} failed: {e}")
    if saved:
        logger.debug(f"💾 Persisted {saved} active chats")


async def preload_active_users_wrapper():
    from database import preload_active_users
    await preload_active_users()


# ══════════════════════════════════════════════════════════════
# ⭐ AUTO-RESET MATCHING STATE — PARALLEL + TIMEOUT
# ══════════════════════════════════════════════════════════════
async def _auto_reset_matching_state():
    """Fast + timeout-protected matching state reset."""
    logger.info("🔧 Auto-reset: starting...")
    try:
        from state import queue, queue_set, queue_lock, last_next_time

        async with queue_lock:
            queue_len_before = len(queue)
            queue.clear()
            queue_set.clear()
        last_next_time.clear()

        try:
            from state import queue_buckets
            for pref_bucket in queue_buckets.values():
                for gender_set in pref_bucket.values():
                    gender_set.clear()
        except Exception:
            pass

        logger.info(
            f"🔄 Matching state auto-reset: cleared {queue_len_before} queue entries"
        )

        from database import users_collection as _uc
        if _uc is None:
            logger.warning("⚠️ Auto-reset: DB not connected — memory only")
            return

        # ⭐ PARALLEL + TIMEOUT — all 3 DB ops simultaneously
        logger.info("🔧 Auto-reset: running 3 DB updates in parallel...")
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _uc.update_many(
                        {"state": {"$in": ["SEARCHING", "MATCHING"]}},
                        {"$set": {"state": "IDLE"}},
                    ),
                    _uc.update_many(
                        {
                            "is_vip": {"$ne": True},
                            "pref_gender": {"$in": ["Male", "Female"]},
                        },
                        {"$set": {"pref_gender": "Any"}},
                    ),
                    _uc.update_many(
                        {"state": {"$ne": "CHAT"}, "partner": {"$ne": None}},
                        {"$set": {"partner": None}},
                    ),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )

            # Log results — non-fatal
            if len(results) >= 1 and not isinstance(results[0], Exception):
                if results[0].modified_count > 0:
                    logger.warning(
                        f"🔄 Reset {results[0].modified_count} users "
                        f"SEARCHING/MATCHING → IDLE"
                    )
            if len(results) >= 2 and not isinstance(results[1], Exception):
                if results[1].modified_count > 0:
                    logger.warning(
                        f"🔄 Reset {results[1].modified_count} non-VIP pref_gender → Any"
                    )
            if len(results) >= 3 and not isinstance(results[2], Exception):
                if results[2].modified_count > 0:
                    logger.warning(
                        f"🔄 Cleared {results[2].modified_count} orphan partner pointers"
                    )

            # Warn if any failed
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.warning(f"⚠️ Auto-reset query {i} failed: {r}")

        except asyncio.TimeoutError:
            logger.warning("⚠️ Auto-reset DB queries TIMEOUT (5s) — skipping")
        except Exception as e:
            logger.error(f"Auto-reset DB updates failed: {e}")

        logger.info("✅ Auto-reset: complete")
    except Exception as e:
        logger.error(f"Auto-reset matching state failed: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════
# ⭐ POST_INIT — TIMEOUT-PROTECTED, NON-FATAL
# ══════════════════════════════════════════════════════════════
async def post_init(application):
    """Idempotent, timeout-protected, non-fatal post_init."""
    global _POST_INIT_DONE

    if _POST_INIT_DONE:
        logger.warning("⚠️ post_init already executed — SKIPPING duplicate call")
        return

    logger.info("=" * 70)
    logger.info("🔧🔧🔧 POST_INIT CALLED")
    logger.info("=" * 70)

    try:
        # ── STEP 1: DB Init (15s timeout) ──
        logger.info("🔧 STEP 1/8: init_db()")
        try:
            await asyncio.wait_for(init_db(), timeout=15.0)
            logger.info("✅ STEP 1/8 DONE: init_db")
        except asyncio.TimeoutError:
            logger.warning("⚠️ STEP 1/8 TIMEOUT — continuing")
        except Exception as e:
            logger.error(f"⚠️ STEP 1/8 FAILED: {e} — continuing")

        # ── STEP 2: Admin Cache (10s timeout) ──
        logger.info("🔧 STEP 2/8: refresh_admin_cache()")
        try:
            await asyncio.wait_for(refresh_admin_cache(), timeout=10.0)
            logger.info(f"✅ STEP 2/8 DONE: {len(admin_cache)} admins")
        except asyncio.TimeoutError:
            logger.warning("⚠️ STEP 2/8 TIMEOUT — continuing")
        except Exception as e:
            logger.error(f"⚠️ STEP 2/8 FAILED: {e} — continuing")

        # ── STEP 3: Preload Users (15s timeout) ──
        logger.info("🔧 STEP 3/8: preload_active_users()")
        try:
            await asyncio.wait_for(preload_active_users_wrapper(), timeout=15.0)
            logger.info(f"✅ STEP 3/8 DONE: {len(users)} users in memory")
        except asyncio.TimeoutError:
            logger.warning("⚠️ STEP 3/8 TIMEOUT — continuing with partial preload")
        except Exception as e:
            logger.error(f"⚠️ STEP 3/8 FAILED: {e} — continuing")

        # ── STEP 4: Auto Reset (10s timeout) ──
        logger.info("🔧 STEP 4/8: auto_reset_matching_state()")
        try:
            await asyncio.wait_for(_auto_reset_matching_state(), timeout=10.0)
            logger.info("✅ STEP 4/8 DONE: auto_reset")
        except asyncio.TimeoutError:
            logger.warning("⚠️ STEP 4/8 TIMEOUT — continuing")
        except Exception as e:
            logger.error(f"⚠️ STEP 4/8 FAILED: {e} — continuing")

        # ── STEP 5: Indexes (10s each) ──
        logger.info("🔧 STEP 5/8: gift + report indexes")
        try:
            await asyncio.wait_for(ensure_gift_indexes(), timeout=10.0)
            logger.info("✅ STEP 5/8a DONE: gift indexes")
        except asyncio.TimeoutError:
            logger.warning("⚠️ Gift indexes TIMEOUT")
        except Exception as e:
            logger.error(f"Gift index failed: {e}")

        try:
            from services.moderation import ensure_report_indexes
            await asyncio.wait_for(ensure_report_indexes(), timeout=10.0)
            logger.info("✅ STEP 5/8b DONE: report indexes")
        except asyncio.TimeoutError:
            logger.warning("⚠️ Report indexes TIMEOUT")
        except Exception as e:
            logger.error(f"Report index failed: {e}")

        # ── STEP 6: Scheduled (10s) ──
        logger.info("🔧 STEP 6/8: load_scheduled_from_db()")
        try:
            await asyncio.wait_for(load_scheduled_from_db(), timeout=10.0)
            logger.info("✅ STEP 6/8 DONE: scheduled broadcasts loaded")
        except asyncio.TimeoutError:
            logger.warning("⚠️ STEP 6/8 TIMEOUT")
        except Exception as e:
            logger.error(f"Scheduled load failed: {e}")

        # ── STEP 7: Bad Words (10s) ──
        logger.info("🔧 STEP 7/8: load_bad_words()")
        try:
            from database import load_bad_words
            bw_count = await asyncio.wait_for(load_bad_words(), timeout=10.0)
            logger.info(f"✅ STEP 7/8 DONE: {bw_count} bad word rules")
        except asyncio.TimeoutError:
            logger.warning("⚠️ STEP 7/8 TIMEOUT")
        except Exception as e:
            logger.error(f"Bad words load failed: {e}")

        # ── STEP 8: Commands + Jobs ──
        logger.info("🔧 STEP 8/8: commands + jobs")

        try:
            from state import broadcast_state
            if broadcast_state.get("running"):
                logger.warning("⚠️ Stale broadcast state — resetting")
                broadcast_state.update({
                    "running": False, "stop_requested": False,
                    "paused": False, "cancel_requested": False,
                    "type": None, "started_at": None,
                    "processed": 0, "total": 0,
                    "sent": 0, "failed": 0, "admin_uid": None,
                })
        except Exception as e:
            logger.error(f"Broadcast recovery failed: {e}")

        analytics["start_time"] = utcnow()

        # Set commands (10s timeout)
        logger.info("🔧 Setting bot commands...")
        try:
            normal_cmds = get_user_commands()
            admin_cmds = get_admin_commands()

            await asyncio.wait_for(
                application.bot.set_my_commands(
                    normal_cmds, scope=BotCommandScopeDefault()
                ),
                timeout=10.0,
            )
            await asyncio.wait_for(
                application.bot.set_chat_menu_button(
                    menu_button=MenuButtonCommands()
                ),
                timeout=10.0,
            )
            logger.info("✅ User commands set")

            for admin_id in admin_cache:
                if admin_id:
                    try:
                        await asyncio.wait_for(
                            application.bot.set_my_commands(
                                admin_cmds, scope=BotCommandScopeChat(chat_id=admin_id)
                            ),
                            timeout=5.0,
                        )
                    except Exception as e:
                        logger.debug(f"Admin cmds for {admin_id} failed: {e}")
            logger.info(f"✅ Admin commands set for {len(admin_cache)} admins")
        except Exception as e:
            logger.error(f"Command setup failed: {e}", exc_info=True)

        # Register jobs — NON-FATAL
        logger.info(f"🔧 JOB QUEUE STATUS: {'✅ PRESENT' if application.job_queue else '❌ NONE'}")

        if application.job_queue:
            logger.info("🔧 Registering all jobs...")
            try:
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

                # ⭐⭐⭐ CRITICAL JOB — background_matcher
                application.job_queue.run_repeating(
                    background_matcher, interval=0.3, first=0.1
                )
                logger.info("✅ ⭐ background_matcher ACTIVE!")

                application.job_queue.run_repeating(
                    flush_writes, interval=5, first=10
                )
                application.job_queue.run_repeating(
                    check_scheduled_broadcasts,
                    interval=SCHEDULED_BROADCAST_CHECK_INTERVAL,
                    first=30,
                )
                application.job_queue.run_repeating(
                    persist_active_chats, interval=30, first=15,
                    name="persist_active_chats",
                )

                try:
                    from services.moderation import send_report_digest
                    application.job_queue.run_repeating(
                        send_report_digest,
                        interval=15 * 60,
                        first=60,
                        name="report_digest",
                    )
                    logger.info("✅ Report digest job scheduled")
                except Exception as e:
                    logger.error(f"Report digest setup failed: {e}")

                try:
                    application.job_queue.run_daily(
                        reset_daily_analytics,
                        time=dt_time(hour=0, minute=0, second=0, tzinfo=timezone.utc),
                        name="daily_analytics_reset",
                    )
                    logger.info("✅ Daily analytics job scheduled")
                except Exception as e:
                    logger.error(f"Daily analytics schedule failed: {e}")

                logger.info("=" * 70)
                logger.info("✅ ALL JOBS REGISTERED")
                logger.info("=" * 70)
            except Exception as e:
                logger.error(f"❌ Job registration failed: {e}", exc_info=True)
        else:
            logger.error("=" * 70)
            logger.error("❌❌❌ JOB QUEUE IS NONE!")
            logger.error("❌ background_matcher WILL NOT RUN!")
            logger.error("=" * 70)

        logger.info("🚀 SparkTalks initialized")

        # Log to channel — with timeout, non-fatal
        try:
            try:
                chat_users = sum(1 for u in users.values() if u.get("partner"))
            except Exception:
                chat_users = 0
            await asyncio.wait_for(
                log_event(
                    application, "BOT_START",
                    category="system", level="info",
                    details={
                        "Users loaded": len(users),
                        "Active chats": chat_users,
                        "Admins": len(admin_cache),
                        "Webhook": WEBHOOK_URL or "polling",
                        "Matching": "v2 simple (no buckets)",
                    },
                ),
                timeout=10.0,
            )
            logger.info("✅ BOT_START logged to channel")
        except asyncio.TimeoutError:
            logger.warning("⚠️ BOT_START log TIMEOUT (skipped)")
        except Exception as e:
            logger.error(f"Bot start log failed: {e}")

        # ⭐ Mark done
        _POST_INIT_DONE = True

        logger.info("=" * 70)
        logger.info("✅✅✅ POST_INIT COMPLETE — BOT READY ✅✅✅")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"❌❌❌ POST_INIT FATAL: {e}", exc_info=True)
        logger.warning("⚠️ Continuing with PARTIAL initialization")
        # DON'T raise — allow bot to start


async def post_shutdown(application):
    logger.info("🔧 post_shutdown called")
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
    err_str = str(err).lower()

    if "conflict" in err_str or "terminated by other" in err_str:
        logger.warning("⚠️ 409 Conflict (deploy overlap) — expected")
        return

    import httpx
    from telegram.error import NetworkError, TimedOut, RetryAfter
    if isinstance(err, (NetworkError, TimedOut, httpx.ReadError, httpx.ConnectError)):
        logger.warning(f"🌐 Network error (auto-retry): {type(err).__name__}")
        return

    if isinstance(err, RetryAfter):
        logger.info(f"⏱ Rate limited — retry in {err.retry_after}s")
        return

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
    logger.info("🔧 run_web: starting Flask...")
    try:
        from web.dashboard import create_dashboard_app
        from database import mongo_client

        logger.info(f"🔧 Creating dashboard app on port {PORT}...")
        app, socketio = create_dashboard_app(
            mongo_client, users, admin_cache, analytics
        )
        logger.info("✅ Dashboard app created")

        socketio.run(
            app,
            host="0.0.0.0",
            port=PORT,
            debug=False,
            use_reloader=False,
            log_output=False,
            allow_unsafe_werkzeug=True,
        )
    except Exception as e:
        logger.error(f"❌ Flask start failed: {e}", exc_info=True)


def _register_handlers(app):
    logger.info("🔧 Registering handlers...")

    # User commands
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

    # Admin: user management
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

    # Admin: moderation
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
    app.add_handler(CommandHandler("endallchats", cmd_endallchats))

    # Admin: analytics
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("topusers", cmd_topusers))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("vip", cmd_vip))
    app.add_handler(CommandHandler("waiting", cmd_waiting))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("reports", cmd_reports))

    # Admin: communication
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

    # Admin: admin management
    app.add_handler(CommandHandler("setadmin", cmd_setadmin))
    app.add_handler(CommandHandler("removeadmin", cmd_removeadmin))
    app.add_handler(CommandHandler("adminlist", cmd_adminlist))

    # Admin: system
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("clearchat", cmd_clearchat))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("clearcache", cmd_clearcache))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("logtest", cmd_logtest))
    app.add_handler(CommandHandler("resetmatching", cmd_resetmatching))
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
    app.add_handler(CommandHandler("chatdb", cmd_chatdb))
    app.add_handler(CommandHandler("debug_owner", cmd_debug_owner))

    # Admin: bad words
    app.add_handler(CommandHandler("banword", cmd_banword))
    app.add_handler(CommandHandler("muteword", cmd_muteword))
    app.add_handler(CommandHandler("removeword", cmd_removeword))
    app.add_handler(CommandHandler("wordlist", cmd_wordlist))

    # Event handlers
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

    logger.info("✅ All handlers registered")


# ══════════════════════════════════════════════════════════════
# ⭐ _ASYNC_MAIN — FINAL PRODUCTION VERSION
# ══════════════════════════════════════════════════════════════
async def _async_main():
    logger.info("=" * 70)
    logger.info("🔧🔧🔧 _ASYNC_MAIN STARTED")
    logger.info("=" * 70)

    if not TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN missing!")
        return

    # ═══ 1. Build app (NO post_init in builder — PTB v22.8 bug) ═══
    logger.info("🔧 Building ApplicationBuilder...")
    bot_app = (
        ApplicationBuilder()
        .token(TOKEN)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .post_shutdown(post_shutdown)
        .build()
    )

    logger.info(f"🔧 post_shutdown callback: {bot_app.post_shutdown}")

    # ═══ 2. Register handlers ═══
    _register_handlers(bot_app)

    # ═══ 3. Register web context EARLY ═══
    logger.info("🔧 Pre-registering web context for /health...")
    from web.dashboard import set_bot_context
    set_bot_context(bot_app, asyncio.get_running_loop())
    logger.info("✅ Bot context pre-registered")

    # ═══ 4. Start Flask FIRST (health 200 during startup) ═══
    logger.info("🔧 Starting Flask web server thread...")
    Thread(target=run_web, daemon=True).start()
    logger.info(f"🌐 Flask thread started (port {PORT})")

    # Wait for Flask to bind
    await asyncio.sleep(1.0)
    logger.info("🔧 Flask bind wait complete")

    # ═══ 5. Initialize bot (without post_init) ═══
    logger.info("🔧 Calling bot_app.initialize()...")
    await bot_app.initialize()
    logger.info("✅ initialize() COMPLETED")

    # ═══ 6. MANUAL POST_INIT with 3 retries ═══
    logger.info("=" * 70)
    logger.info("🔧 MANUALLY calling post_init()...")
    logger.info("=" * 70)

    post_init_success = False
    for attempt in range(1, 4):
        try:
            logger.info(f"🔧 post_init attempt {attempt}/3...")
            await post_init(bot_app)
            post_init_success = True
            logger.info(f"✅✅✅ post_init SUCCESS on attempt {attempt}/3")
            break
        except Exception as e:
            logger.error(
                f"❌ post_init attempt {attempt}/3 FAILED: {e}",
                exc_info=True,
            )
            if attempt < 3:
                logger.info("⏳ Retrying post_init in 2 seconds...")
                await asyncio.sleep(2.0)

    if not post_init_success:
        logger.critical("=" * 70)
        logger.critical("🚨 POST_INIT FAILED ALL 3 ATTEMPTS — bot runs WITHOUT matching")
        logger.critical("=" * 70)

    # ═══ 7. Start bot ═══
    logger.info("🔧 Calling bot_app.start()...")
    await bot_app.start()
    logger.info("✅ start() COMPLETED")

    # ═══ 8. Webhook / polling ═══
    if WEBHOOK_URL:
        webhook_full_url = f"{WEBHOOK_URL}/tg-webhook"
        logger.info(f"🔧 Setting webhook: {webhook_full_url}")
        try:
            skip_set = False
            try:
                current = await bot_app.bot.get_webhook_info()
                if current.url == webhook_full_url:
                    skip_set = True
                    logger.info(f"✅ Webhook already set: {webhook_full_url}")
            except Exception as e:
                logger.debug(f"get_webhook_info failed: {e}")

            if not skip_set:
                await bot_app.bot.set_webhook(
                    url=webhook_full_url,
                    secret_token=WEBHOOK_SECRET or None,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
                logger.info(f"✅ WEBHOOK SET: {webhook_full_url}")

            logger.info("🚀 Zero-gap deploy active")
        except Exception as e:
            logger.error(f"❌ Webhook set failed: {e}")
            logger.warning("⚠️ Falling back to polling mode")
            await bot_app.updater.start_polling(
                drop_pending_updates=False,
                allowed_updates=Update.ALL_TYPES,
            )
    else:
        logger.warning("⚠️ WEBHOOK_URL not set — using polling fallback")
        await bot_app.updater.start_polling(
            drop_pending_updates=False,
            allowed_updates=Update.ALL_TYPES,
        )

    logger.info("=" * 70)
    if post_init_success:
        logger.info("🚀🚀🚀 SparkTalks FULLY ONLINE — MATCHING ACTIVE 🚀🚀🚀")
    else:
        logger.warning("⚠️⚠️⚠️ SparkTalks ONLINE — MATCHING DISABLED ⚠️⚠️⚠️")
    logger.info("=" * 70)

    # ═══ 9. Wait for shutdown ═══
    shutdown_event = asyncio.Event()

    def _on_sigterm():
        logger.warning("🛑 SIGTERM — graceful shutdown")
        shutdown_event.set()

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _on_sigterm)
    except (NotImplementedError, AttributeError):
        logger.warning("⚠️ SIGTERM handler not available")

    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("🛑 Shutting down gracefully...")

    try:
        await log_event(
            bot_app, "BOT_STOP",
            category="system", level="warn",
            details={"Reason": "graceful shutdown / SIGTERM"},
        )
    except Exception:
        pass

    # Parallel shutdown save
    try:
        from database import _do_save
        chat_users = [(uid, u) for uid, u in users.items() if u.get("partner")]
        saved = 0
        failed = 0

        if chat_users:
            SHUTDOWN_BUDGET = 20.0
            BATCH_SIZE = 15
            start_ts = asyncio.get_event_loop().time()
            skipped = 0

            for i in range(0, len(chat_users), BATCH_SIZE):
                elapsed = asyncio.get_event_loop().time() - start_ts
                if elapsed > SHUTDOWN_BUDGET:
                    skipped = len(chat_users) - i
                    logger.warning(
                        f"⚠️ Shutdown save budget exceeded — "
                        f"{skipped} pairs skipped"
                    )
                    break

                chunk = chat_users[i:i + BATCH_SIZE]
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(
                            *[_do_save(uid, u, pop_queue=True)
                              for uid, u in chunk],
                            return_exceptions=True,
                        ),
                        timeout=5.0,
                    )
                    saved += sum(1 for r in results if r is True)
                    failed += sum(1 for r in results if r is not True)
                except asyncio.TimeoutError:
                    failed += len(chunk)
                    logger.warning(
                        f"⚠️ Shutdown save batch {i // BATCH_SIZE + 1} timed out"
                    )

            logger.info(
                f"💾 Shutdown save: {saved} chats saved, {failed} failed, "
                f"{skipped} skipped ({len(chat_users)} total)"
            )
    except Exception as e:
        logger.error(f"Shutdown save failed: {e}", exc_info=True)

    try:
        if bot_app.updater and bot_app.updater.running:
            await bot_app.updater.stop()
    except Exception:
        pass

    await bot_app.stop()
    await bot_app.shutdown()

    logger.info("✅ Shutdown complete")


def main():
    logger.info("=" * 70)
    logger.info("🔧🔧🔧 MAIN() STARTING")
    logger.info("=" * 70)
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("⏹ Interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    main()