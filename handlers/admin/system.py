"""System: health, logs, purge, force delete, appeals, gifts, VIP info, invites, chatdb, debug_owner, reports, logtest."""
import asyncio
import logging
from datetime import timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import OWNER_ID, ADMIN_IDS, LOG_CHANNEL_ID
from state import (
    users, queue, queue_set, queue_lock, admin_cache,
    muted_users, admin_logs, analytics, scheduled_broadcasts,
)
import state
from database import (
    is_owner_or_admin, users_collection, resolve_user,
    save_user_to_db,
)
from utils import safe_send, utcnow, esc, log_event, _resolve_log_channel
from handlers.admin._helpers import (
    _sidebar, _safe_reply, _log_action, _deny, _require_db, _not_found,
)

logger = logging.getLogger("sparktalks")


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    mem_str = "N/A"
    try:
        import psutil
        proc = psutil.Process()
        mem_str = f"{proc.memory_info().rss / 1024 / 1024:.1f} MB"
    except Exception:
        pass

    db_ok = "❌ Down"
    if users_collection is not None:
        try:
            await asyncio.wait_for(
                users_collection.find_one({}, {"_id": 1}), timeout=2.0,
            )
            db_ok = "✅ OK"
        except asyncio.TimeoutError:
            db_ok = "⚠️ Timeout"
        except Exception:
            db_ok = "⚠️ Unreachable"

    maint = "🚧 ON" if state.maintenance_mode else "✅ OFF"
    uptime = "—"
    if analytics.get("start_time"):
        delta = utcnow() - analytics["start_time"]
        uptime = str(delta).split(".")[0]
    log_ch = str(_resolve_log_channel() or "❌ Not configured")
    await update.message.reply_text(_sidebar(
        "System Health", "💚",
        ["🖥️ <b>Bot</b>",
         f"  ├ Memory : {mem_str}",
         f"  └ Uptime : {uptime}",
         "",
         "🗄️ <b>Database</b>",
         f"  ├ Connection : {db_ok}",
         f"  └ Users cached : {len(users)}",
         "",
         "📋 <b>Runtime</b>",
         f"  ├ Queue : {len(queue)}",
         f"  ├ Admins : {len(admin_cache)}",
         f"  ├ Muted : {len(muted_users)}",
         f"  ├ Scheduled : {len(scheduled_broadcasts)}",
         f"  ├ Log channel : {esc(log_ch)}",
         f"  └ Maintenance : {maint}"],
    ), parse_mode="HTML")


async def cmd_clearchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /clearchat ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    u = users.get(target_id)
    if not u:
        return await update.message.reply_text("User offline.")

    try:
        from services.matching import _delete_searching_card
        await _delete_searching_card(context, target_id)
    except Exception:
        pass

    if u.get("partner"):
        pu = users.get(u["partner"])
        if pu:
            pu["partner"] = None
            pu["state"] = "IDLE"
    u["partner"] = None
    u["state"] = "IDLE"
    u["pending_media"] = {}
    async with queue_lock:
        try:
            queue.remove(target_id)
        except ValueError:
            pass
        queue_set.discard(target_id)

    await save_user_to_db(target_id, u)

    try:
        await log_event(
            context, "CLEARCACHE",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={"Action": "clearchat single user"},
        )
    except Exception:
        pass

    await update.message.reply_text("✅ State cleared.")


async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not context.args:
        status = "🚧 ON" if state.maintenance_mode else "✅ OFF"
        return await update.message.reply_text(
            _sidebar("Maintenance", "🚧", [
                f"Current: {status}",
                "Usage: /maintenance on|off",
                "",
                "⚠️ <i>Memory-only — resets on bot restart.</i>",
            ]), parse_mode="HTML",
        )
    arg = context.args[0].lower()
    if arg in ("on", "true", "1"):
        state.maintenance_mode = True
        try:
            await log_event(
                context, "MAINTENANCE_ON",
                category="admin", level="warn",
                actor_id=update.effective_user.id,
            )
        except Exception:
            pass
        await update.message.reply_text("🚧 Maintenance ON. (resets on restart)")
    elif arg in ("off", "false", "0"):
        state.maintenance_mode = False
        try:
            await log_event(
                context, "MAINTENANCE_OFF",
                category="admin", level="info",
                actor_id=update.effective_user.id,
            )
        except Exception:
            pass
        await update.message.reply_text("✅ Maintenance OFF.")


async def cmd_clearcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all caches — v2 SIMPLE MATCHING.

    ⭐ FIXES:
      - Persist partner=None to DB before clearing memory
      - No bucket cleanup needed (v2 matching)
      - Flush pending writes AFTER loop (not before)
    """
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    from state import last_next_time, message_reactions_map, message_edit_map

    # ⭐ Flush pending writes FIRST (safe — old queue entries)
    flushed = 0
    try:
        from database import flush_writes, _pending_writes
        flushed = len(_pending_writes)
        await flush_writes()
        logger.info(f"💾 Flushed {flushed} pending writes before clearcache")
    except Exception as e:
        logger.error(f"clearcache flush failed: {e}")

    # ⭐ Disconnect + PERSIST each active chat
    disconnected = 0
    for u_id, u in list(users.items()):
        if u.get("partner"):
            try:
                await safe_send(
                    context, u_id,
                    _sidebar("Cache Cleared", "🧹", [
                        "🧹 Admin cleared cache.",
                        "",
                        "💡 Your chat was reset. Use /next to start fresh.",
                    ]),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            u["partner"] = None
            u["state"] = "IDLE"
            u["pending_media"] = {}
            # ⭐ Persist to DB so restart doesn't resurrect
            try:
                await save_user_to_db(u_id, u)
            except Exception as e:
                logger.error(f"clearcache persist {u_id} failed: {e}")
            disconnected += 1

    # ⭐ Flush the new writes
    try:
        from database import flush_writes
        await flush_writes()
    except Exception as e:
        logger.error(f"clearcache post-flush failed: {e}")

    cleared = len(users)
    users.clear()
    last_next_time.clear()
    message_reactions_map.clear()
    message_edit_map.clear()
    muted_users.clear()

    queue_count = len(queue)
    queue.clear()
    queue_set.clear()

    # Clear buckets if they exist (backward compat)
    try:
        from state import queue_buckets
        for pref_bucket in queue_buckets.values():
            for gender_set in pref_bucket.values():
                gender_set.clear()
    except Exception:
        pass

    try:
        await log_event(
            context, "CLEARCACHE",
            category="admin", level="critical",
            actor_id=update.effective_user.id,
            details={
                "Users cleared": cleared,
                "Chats disconnected": disconnected,
                "Queue entries": queue_count,
                "Pending flushed": flushed,
            },
        )
    except Exception:
        pass

    await update.message.reply_text(
        _sidebar("Cache Cleared", "🧹", [
            f"✅ Cleared {cleared} users.",
            f"✅ Disconnected {disconnected} chats.",
            f"✅ Cleared {queue_count} queue entries.",
            f"✅ Flushed {flushed} pending writes.",
            "",
            "💡 Users will reload from DB on next message.",
        ]),
        parse_mode="HTML",
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    count = 10
    if context.args:
        try:
            count = min(int(context.args[0]), 50)
        except ValueError:
            pass
    if not admin_logs:
        return await update.message.reply_text("📭 No logs yet.")
    lines = [f"Last {min(count, len(admin_logs))} actions", ""]
    for entry in list(admin_logs)[-count:]:
        lines.append(f"[{entry['time']}] <code>{entry['admin']}</code>")
        lines.append(f"  └ {esc(entry['action'])}")
    await _safe_reply(update, _sidebar("Admin Logs", "📝", lines))


async def cmd_forcedelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not context.args:
        return await update.message.reply_text(
            _sidebar("Force Delete", "🗑️", [
                "📌 <b>Usage:</b>",
                "<code>/forcedelete user_id</code>",
                "",
                "⚠️ <b>This will permanently delete:</b>",
                "  ├ Profile + stats",
                "  ├ VIP status",
                "  ├ Saved partners",
                "  ├ Achievements",
                "  ├ Chat history",
                "  ├ Queue position",
                "  ├ Voice rooms",
                "  └ All caches",
                "",
                "❗ Cannot be undone!",
            ], tip="Use <code>/userinfo ID</code> to confirm first"),
            parse_mode="HTML",
        )

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Invalid user ID.",
                f"Got: <code>{esc(context.args[0])}</code>",
            ]), parse_mode="HTML",
        )

    if target_id == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", ["Cannot delete the Owner account!"]),
            parse_mode="HTML",
        )

    if target_id == update.effective_user.id:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", [
                "You cannot delete yourself!",
                "Use /cancel instead (self-service).",
            ]), parse_mode="HTML",
        )

    in_memory = target_id in users
    in_db = False
    if users_collection is not None:
        try:
            doc = await users_collection.find_one({"user_id": target_id}, {"_id": 1})
            in_db = doc is not None
        except Exception:
            pass

    if not in_memory and not in_db:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍", [
                f"User <code>{target_id}</code> does not exist",
                "in memory or database.",
            ]), parse_mode="HTML",
        )

    context.user_data["forcedelete_target"] = target_id

    where = []
    if in_memory:
        where.append("✅ Memory")
    if in_db:
        where.append("✅ Database")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🗑️ Yes, Delete Permanently",
            callback_data=f"FD_CONFIRM:{target_id}",
        ),
    ], [
        InlineKeyboardButton("❌ Cancel", callback_data="FD_CANCEL"),
    ]])

    await update.message.reply_text(
        _sidebar("Confirm Force Delete", "⚠️", [
            f"🆔 User : <code>{target_id}</code>",
            f"📦 Found in : {' · '.join(where)}",
            "",
            "⚠️ <b>This action is irreversible!</b>",
            "All data will be permanently removed.",
        ], tip="Tap confirm below to proceed"),
        reply_markup=kb, parse_mode="HTML",
    )


async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not context.args:
        return await update.message.reply_text(
            _sidebar("Purge User", "🧨", [
                "📌 <b>Usage:</b>",
                "<code>/purge user_id</code>",
                "",
                "⚠️ <b>Immediately deletes:</b>",
                "  ├ Profile + stats",
                "  ├ VIP status",
                "  ├ Saved partners",
                "  ├ Blocked users",
                "  ├ Achievements",
                "  ├ Queue position",
                "  ├ Chat sessions",
                "  ├ Voice rooms",
                "  └ All caches",
                "",
                "🎯 <b>Result:</b>",
                "  └ User looks brand new next time",
                "     (gender setup required)",
                "",
                "❗ No confirmation — be careful!",
            ], tip="For confirmation flow, use <code>/forcedelete</code>"),
            parse_mode="HTML",
        )

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Invalid user ID.",
                f"Got: <code>{esc(context.args[0])}</code>",
            ]), parse_mode="HTML",
        )

    if target_id == OWNER_ID:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", ["Cannot purge the Owner account!"]),
            parse_mode="HTML",
        )

    if target_id == update.effective_user.id:
        return await update.message.reply_text(
            _sidebar("Denied", "🚫", [
                "You cannot purge yourself!",
                "Use /cancel instead.",
            ]), parse_mode="HTML",
        )

    progress_msg = await update.message.reply_text(
        _sidebar("Purging...", "⏳", [
            f"🆔 User : <code>{target_id}</code>",
            "",
            "⏳ Removing all data...",
        ]),
        parse_mode="HTML",
    )

    try:
        from services.user_deletion import delete_user_completely
        stats = await asyncio.wait_for(
            delete_user_completely(target_id),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Purge timed out for {target_id}")
        stats = {
            "was_in_memory": True, "was_in_queue": False, "was_in_db": False,
            "partner_notified": False, "db_deleted": False,
            "memory_cleaned": True, "refs_cleaned": 0,
            "error": "Timed out — memory cleared, DB retry in background",
        }
    except Exception as e:
        logger.error(f"Purge error for {target_id}: {e}", exc_info=True)
        stats = {
            "was_in_memory": False, "was_in_queue": False, "was_in_db": False,
            "partner_notified": False, "db_deleted": False,
            "memory_cleaned": False, "refs_cleaned": 0, "error": str(e),
        }

    lines = [
        f"🆔 User : <code>{target_id}</code>",
        "",
        "📊 <b>Purge Summary</b>",
        f"  ├ Memory : {'✅ Cleared' if stats.get('memory_cleaned') else '❌ Failed'}",
        f"  ├ Queue : {'✅ Removed' if stats.get('was_in_queue') else '⚪ Not in queue'}",
        f"  ├ Database : {'✅ Deleted' if stats.get('db_deleted') else '⚪ Not found'}",
        f"  ├ Partner : {'✅ Cleared' if stats.get('partner_notified') else '⚪ No partner'}",
        f"  └ Refs : <b>{stats.get('refs_cleaned', 0)}</b> cleaned",
        "",
        "🎯 <b>Next Time User /start</b>",
        "  └ ✅ Fresh setup (gender will be asked)",
    ]

    if stats.get("error"):
        lines.append("")
        lines.append(f"⚠️ Note: <code>{esc(stats['error'])}</code>")

    try:
        await progress_msg.edit_text(
            _sidebar("Purged", "🧨", lines), parse_mode="HTML",
        )
    except Exception:
        await update.message.reply_text(
            _sidebar("Purged", "🧨", lines), parse_mode="HTML",
        )

    try:
        await log_event(
            context, "PURGE",
            category="admin", level="critical",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={
                "Memory": "cleared" if stats.get("memory_cleaned") else "failed",
                "DB": "deleted" if stats.get("db_deleted") else "not found",
                "Refs cleaned": stats.get("refs_cleaned", 0),
            },
        )
    except Exception:
        pass

    _log_action(
        update.effective_user.id,
        f"purged user {target_id} (refs_cleaned={stats.get('refs_cleaned', 0)})"
    )
    logger.info(f"🧨 Admin {update.effective_user.id} purged user {target_id}")


async def cmd_getfileid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    msg = update.message
    reply = msg.reply_to_message
    if not reply or not reply.photo:
        return await msg.reply_text(
            _sidebar("Get File ID", "🆔", [
                "📌 <b>How to use:</b>",
                "  1. Send a photo to this bot",
                "  2. Reply to that photo with",
                "     <code>/getfileid</code>",
                "  3. Copy the ID shown",
                "  4. Add to Render env vars:",
                "     <code>WELCOME_IMAGE_FILE_ID=...</code>",
                "     <code>DASHBOARD_IMAGE_FILE_ID=...</code>",
            ]),
            parse_mode="HTML",
        )

    file_id = reply.photo[-1].file_id
    await msg.reply_text(
        _sidebar("File ID Captured", "🆔", [
            "📋 <b>Your File ID:</b>",
            "",
            f"<code>{file_id}</code>",
            "",
            "📌 <b>Next steps:</b>",
            "  ├ Copy the ID above",
            "  ├ Render → Environment",
            "  ├ Add:",
            "  │  <code>WELCOME_IMAGE_FILE_ID=...</code>",
            "  │  <code>DASHBOARD_IMAGE_FILE_ID=...</code>",
            "  └ Save → Redeploy",
            "",
            "💡 <i>Banner will auto-appear after redeploy.</i>",
        ], tip="Copy the ID exactly — no spaces!"),
        parse_mode="HTML",
    )


async def cmd_appeals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    lines = ["Recent banned users:", ""]
    count = 0
    if users_collection is not None:
        async for doc in users_collection.find(
            {"is_banned": True},
            {"user_id": 1, "name": 1, "banned_reason": 1, "banned_at": 1},
        ).sort("banned_at", -1).limit(10):
            name = esc(doc.get("name") or "—")
            reason = esc(doc.get("banned_reason") or "—")
            at = doc.get("banned_at")
            at_str = at.strftime("%d %b %H:%M") if at else "—"
            lines.append(f"• <code>{doc['user_id']}</code> {name}")
            lines.append(f"  └ {reason} ({at_str})")
            count += 1

    if count == 0:
        lines = ["✅ No banned users currently."]

    await update.message.reply_text(
        _sidebar("Appeals / Bans", "⚖️", lines,
                 tip="Use <code>/unban ID</code> to approve appeals"),
        parse_mode="HTML",
    )


async def cmd_resolve_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")
    if not await _require_db(update):
        return

    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Usage", "⚠️", [
                "<code>/resolve ID approve</code>",
                "<code>/resolve ID reject</code>",
            ]), parse_mode="HTML",
        )

    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    action = context.args[1].lower()

    if action in ("approve", "unban", "ok"):
        await users_collection.update_one(
            {"user_id": target_id},
            {"$set": {"is_banned": False, "banned_reason": None, "warnings": []}},
        )
        u = users.get(target_id)
        if u:
            u["is_banned"] = False
            u["warnings"] = []
            await save_user_to_db(target_id, u)
        await safe_send(
            context, target_id,
            _sidebar("Appeal Approved", "✅", [
                "✅ Your ban has been lifted!",
                "",
                "Welcome back — please follow the rules.",
            ]), parse_mode="HTML",
        )
        await update.message.reply_text(
            _sidebar("Approved", "✅", [f"✅ Unbanned <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
        _log_action(update.effective_user.id, f"approved appeal for {target_id}")

        try:
            await log_event(
                context, "UNBAN",
                category="admin", level="info",
                actor_id=update.effective_user.id,
                target_id=target_id,
                details={"Via": "appeal approval"},
            )
        except Exception:
            pass

    elif action in ("reject", "deny", "no"):
        await safe_send(
            context, target_id,
            _sidebar("Appeal Rejected", "❌", [
                "❌ Your appeal was reviewed and denied.",
                "",
                "Contact owner for further queries.",
            ]), parse_mode="HTML",
        )
        await update.message.reply_text(
            _sidebar("Rejected", "❌",
                     [f"❌ Appeal rejected for <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
        _log_action(update.effective_user.id, f"rejected appeal for {target_id}")

    else:
        await update.message.reply_text("⚠️ Use 'approve' or 'reject'.")


async def cmd_checkchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ Owner only.")

    from services.community import _get_channel_chat_id, is_configured
    from config import COMMUNITY_CHANNEL_USERNAME, COMMUNITY_CHANNEL_ID

    if not is_configured():
        return await update.message.reply_text(
            "⚠️ Community channel not configured.\n"
            "Set COMMUNITY_CHANNEL_USERNAME or COMMUNITY_CHANNEL_ID in env.",
            parse_mode="HTML",
        )

    chat_id = _get_channel_chat_id()
    lines = [
        "📢 <b>Channel Config</b>",
        f"  ├ Username : {esc(COMMUNITY_CHANNEL_USERNAME or '—')}",
        f"  ├ ID : {esc(COMMUNITY_CHANNEL_ID or '—')}",
        f"  └ Resolved : <code>{chat_id}</code>",
        "",
        "🔍 <b>Testing access...</b>",
    ]

    try:
        chat = await context.bot.get_chat(chat_id)
        lines.append(f"✅ Bot can access: <b>{esc(chat.title)}</b>")
        lines.append(f"  ├ Type : {chat.type}")
        lines.append(f"  └ ID : <code>{chat.id}</code>")

        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat_id, me.id)

        status = getattr(member, "status", "?")
        if hasattr(status, "value"):
            status = status.value
        lines.append("")
        lines.append("👤 <b>Bot Status in Channel</b>")
        lines.append(f"  ├ Status : <b>{status}</b>")

        if str(status).lower() in ("administrator", "creator"):
            lines.append("  └ ✅ <b>PERFECT — Bot is admin!</b>")
        else:
            lines.append("  └ ❌ <b>Bot is NOT admin — verify will fail!</b>")

    except Exception as e:
        lines.append(f"❌ <b>FAILED:</b> <code>{esc(str(e)[:200])}</code>")
        lines.append("")
        lines.append("💡 <b>Fix:</b>")
        lines.append("  1. Channel → Manage → Administrators")
        lines.append("  2. Add Admin → search bot username")
        lines.append("  3. Add with default permissions")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_gifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    from services.gifts import get_user_gifts, get_recent_gifts, count_user_gifts

    if context.args:
        target_id, doc = await resolve_user(context.args[0])
        if not target_id:
            return await _not_found(update, context.args[0])

        gifts = await get_user_gifts(target_id, limit=30)
        total = await count_user_gifts(target_id)

        if not gifts:
            return await update.message.reply_text(
                _sidebar("Gifts Received", "🎁", [
                    f"👤 User : <code>{target_id}</code>",
                    "",
                    "📭 No gifts received yet.",
                ]), parse_mode="HTML",
            )

        name = esc(doc.get("name") or "Unknown")
        uname = doc.get("username")
        uname_str = esc(f"@{uname}") if uname else "—"

        lines = [
            f"👤 User : <b>{name}</b>",
            f"🆔 ID : <code>{target_id}</code>",
            f"🔗 Username : {uname_str}",
            "",
            f"📊 Total gifts : <b>{total}</b>",
            f"📋 Showing : <b>{len(gifts)}</b> recent",
            "",
            "🎁 <b>Gift History</b>",
        ]
        for g in gifts[:15]:
            at = g.get("at")
            at_str = at.strftime("%d %b %Y %H:%M") if at else "—"
            gtype = esc(str(g.get("gift_type", "Gift")))
            gval = esc(str(g.get("gift_value", "?")))
            reason = esc(g.get("reason", "—"))
            lines.append(f"  • <b>+{gval} {gtype}</b>")
            lines.append(f"    └ {reason}")
            lines.append(f"    🕐 {at_str}")
            lines.append("")

        await _safe_reply(update, _sidebar("Gifts Received", "🎁", lines))
        return

    gifts = await get_recent_gifts(limit=20)
    if not gifts:
        return await update.message.reply_text(
            _sidebar("Recent Gifts", "🎁", [
                "📭 No gifts sent yet.",
                "", "💡 Gifts are logged when:",
                "  ├ User hits referral milestone",
                "  └ Bonus events",
            ]), parse_mode="HTML",
        )

    lines = [
        f"Total recent : <b>{len(gifts)}</b>",
        "",
        "🎁 <b>Latest Gifts</b>",
    ]
    for g in gifts:
        uid_ = g.get("user_id")
        at = g.get("at")
        at_str = at.strftime("%d %b %H:%M") if at else "—"
        gtype = esc(str(g.get("gift_type", "Gift")))
        gval = esc(str(g.get("gift_value", "?")))
        name_lookup = "—"
        if users_collection is not None:
            try:
                u_doc = await users_collection.find_one(
                    {"user_id": uid_}, {"name": 1}
                )
                if u_doc:
                    name_lookup = esc(u_doc.get("name") or "—")
            except Exception:
                pass
        lines.append(f"  • <code>{uid_}</code> ({name_lookup}) +{gval} {gtype} ({at_str})")

    lines.append("")
    lines.append("💡 Use <code>/gifts user_id</code> for details")
    await _safe_reply(update, _sidebar("Recent Gifts", "🎁", lines))


_PLAN_DAYS = {"Sprint": 14, "Gold": 30, "Diamond": 90, "Master": 180}


def _infer_plan_from_tier(tier_name: str) -> tuple:
    if not tier_name:
        return None, None
    t = str(tier_name)
    for key, days in _PLAN_DAYS.items():
        if key.lower() in t.lower():
            return key, days
    return None, None


def _infer_grant_date(expiry, days: int):
    if not expiry or not days:
        return None
    try:
        return expiry - timedelta(days=days)
    except Exception:
        return None


async def cmd_vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not context.args:
        return await update.message.reply_text(
            _sidebar("VIP Info", "👑", [
                "📌 <b>Usage:</b>",
                "<code>/vipinfo user_id|@username</code>",
                "",
                "💡 Shows:",
                "  ├ ⭐ Stars purchases",
                "  ├ 🛡️ Admin grants",
                "  ├ 🎁 Referral rewards",
                "  ├ ✨ Bonus rewards",
                "  ├ 📝 Backfilled records",
                "  └ 🕰️ Inferred legacy info",
            ]), parse_mode="HTML",
        )

    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    is_vip = bool(doc.get("is_vip", False))
    tier = doc.get("vip_tier_name") or "None"
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y") if exp else "—"

    plan_label, plan_days = _infer_plan_from_tier(tier)

    now = utcnow()
    days_left = None
    hours_left = None
    days_since_expiry = None
    hours_since_expiry = None
    if exp:
        try:
            seconds = (exp - now).total_seconds()
            if seconds >= 0:
                hours_left = int(seconds // 3600)
                days_left = int(seconds // 86400)
            else:
                seconds = abs(seconds)
                hours_since_expiry = int(seconds // 3600)
                days_since_expiry = int(seconds // 86400)
        except Exception:
            pass

    tier_clean = (tier or "").strip()
    has_tier = bool(tier_clean) and tier_clean.lower() != "none"

    if is_vip:
        display_tier = esc(tier_clean) if has_tier else "Unnamed VIP"
        status = f"👑 {display_tier}"
    else:
        status = f"⌛ Expired · was {esc(tier_clean)}" if has_tier else "⚪ Free Member"

    name = esc(doc.get("name") or "Unknown")
    uname = doc.get("username")
    uname_str = esc(f"@{uname}") if uname else "—"
    joined = doc.get("joined_date")
    joined_str = joined.strftime("%d %b %Y") if joined else "—"

    payment_history = doc.get("payment_history") or []
    gifts = []
    try:
        from services.gifts import get_user_gifts
        gifts = await get_user_gifts(target_id, limit=50)
    except Exception as e:
        logger.debug(f"gift lookup failed: {e}")

    stars_purchases = [h for h in payment_history if h.get("source") == "stars"]
    admin_grants = [h for h in payment_history if h.get("source") == "admin"]
    referral_rewards = [h for h in payment_history if h.get("source") == "referral"]
    bonus_entries = [h for h in payment_history if h.get("source") == "bonus"]
    backfilled = [h for h in payment_history if h.get("source") == "backfill"]
    legacy_entries = [h for h in payment_history if not h.get("source")]

    other_gifts = [
        g for g in gifts
        if "referral" not in str(g.get("reason", "")).lower()
        and "admin granted" not in str(g.get("reason", "")).lower()
        and "[backfill]" not in str(g.get("reason", "")).lower()
    ]

    total_events = (
        len(stars_purchases) + len(admin_grants) + len(referral_rewards)
        + len(bonus_entries) + len(backfilled) + len(legacy_entries)
    )
    is_legacy = (is_vip or has_tier) and total_events == 0

    lines = [
        f"👤 <b>{name}</b>",
        f"🆔 ID : <code>{target_id}</code>",
        f"🔗 Username : {uname_str}",
        f"📅 Joined : {joined_str}",
        "",
        "⭐  <b>Current Status</b>",
        f"  ├ Status : {status}",
        f"  ├ ⌛ Expires : {exp_str}",
    ]
    if days_left is not None:
        if days_left >= 1:
            lines.append(f"  ├ ⏰ Active for : <b>{days_left} days</b> more")
        elif hours_left is not None and hours_left >= 1:
            lines.append(f"  ├ ⏰ Active for : <b>~{hours_left} hours</b> more")
        else:
            lines.append("  ├ ⏰ Active for : <b>< 1 hour</b>")
    elif days_since_expiry is not None:
        if days_since_expiry >= 1:
            lines.append(f"  ├ ⏰ Expired : <b>{days_since_expiry} days ago</b>")
        else:
            lines.append(f"  ├ ⏰ Expired : <b>~{hours_since_expiry} hours ago</b>")
    lines.append(f"  └ 📊 Tracked events : <b>{total_events}</b>")

    if is_legacy:
        lines.append("")
        lines.append("🕰️  <b>Inferred Legacy VIP</b>")
        if has_tier:
            lines.append(f"  ├ Tier : {esc(tier_clean)}")
            if plan_label and plan_days:
                lines.append(f"  ├ Plan type : <b>{plan_label}</b>")
                lines.append(f"  ├ Duration : <b>{plan_days} days</b>")
                inferred = _infer_grant_date(exp, plan_days)
                if inferred:
                    lines.append(f"  └ Est. grant : <b>{inferred.strftime('%d %b %Y')}</b>")
            else:
                lines.append("  └ <i>Unknown plan type</i>")
        else:
            lines.append("  └ <i>No tier info in DB</i>")
        lines.append("")
        lines.append("⚠️  <b>Why no source?</b>")
        lines.append("  ├ VIP granted before tracking")
        lines.append("  ├ Old /addvip (pre-history)")
        lines.append("  └ Manual DB entry")
        lines.append("")
        lines.append("📝 <b>Backfill it:</b>")
        lines.append(
            f"  <code>/vipbackfill {target_id} {plan_days or 30} \"Legacy grant\"</code>"
        )

    if stars_purchases:
        lines.append("")
        lines.append("⭐  <b>Stars Purchases</b>")
        for h in reversed(stars_purchases[-5:]):
            lines.append(f"  • {esc(h.get('tier', 'VIP'))} (+{h.get('days', '?')}d) · {h.get('stars', 0)}⭐")
            lines.append(f"    └ 🕐 {str(h.get('at', ''))[:10]}")

    if admin_grants:
        lines.append("")
        lines.append("🛡️  <b>Admin Grants</b>")
        for h in reversed(admin_grants[-5:]):
            lines.append(f"  • {esc(h.get('tier', 'VIP'))} (+{h.get('days', '?')}d)")
            lines.append(f"    └ 👤 by <code>{h.get('granted_by', '?')}</code> · 🕐 {str(h.get('at', ''))[:10]}")

    if referral_rewards:
        lines.append("")
        lines.append("🎁  <b>Referral Rewards</b>")
        for h in reversed(referral_rewards[-5:]):
            lines.append(f"  • +{h.get('days', '?')}d VIP")
            lines.append(f"    └ {esc(str(h.get('note', '—'))[:70])}")
            lines.append(f"    └ 🕐 {str(h.get('at', ''))[:10]}")

    if bonus_entries:
        lines.append("")
        lines.append("✨  <b>Bonus Rewards</b>")
        for h in reversed(bonus_entries[-5:]):
            lines.append(f"  • +{h.get('days', '?')}d VIP")
            lines.append(f"    └ {esc(str(h.get('note', '—'))[:70])}")
            lines.append(f"    └ 🕐 {str(h.get('at', ''))[:10]}")

    if backfilled:
        lines.append("")
        lines.append("📝  <b>Backfilled Records</b>")
        for h in reversed(backfilled[-5:]):
            lines.append(f"  • +{h.get('days', '?')}d")
            lines.append(f"    └ 📌 {esc(str(h.get('note', '—'))[:60])}")
            lines.append(f"    └ 🕐 {str(h.get('at', ''))[:10]}")

    if legacy_entries:
        lines.append("")
        lines.append("📋  <b>Untagged Records</b>")
        lines.append("  <i>(old data — no source field)</i>")
        for h in reversed(legacy_entries[-5:]):
            stars = h.get("stars", 0)
            tag = f" · {stars}⭐" if stars > 0 else ""
            lines.append(f"  • {esc(h.get('tier', 'VIP'))} (+{h.get('days', '?')}d){tag} · {str(h.get('at', ''))[:10]}")

    if other_gifts:
        lines.append("")
        lines.append("🎁  <b>Other Gifts</b>")
        for g in other_gifts[:5]:
            at = g.get("at")
            at_str = at.strftime("%d %b %Y") if at else "—"
            lines.append(f"  • +{g.get('gift_value', '?')} {esc(str(g.get('gift_type', 'Gift')))}")
            lines.append(f"    └ {esc(str(g.get('reason', '—'))[:60])}")
            lines.append(f"    └ 🕐 {at_str}")

    if (not is_vip and not has_tier and total_events == 0):
        lines.append("")
        lines.append("📭 <i>No VIP history found.</i>")

    await _safe_reply(
        update,
        _sidebar("VIP Source History", "👑", lines,
                 tip="Complete VIP record + legacy inference"),
    )


async def cmd_vipbackfill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return

    if len(context.args) < 3:
        return await update.message.reply_text(
            _sidebar("VIP Backfill", "📝", [
                "📌 <b>Usage:</b>",
                "<code>/vipbackfill user_id days reason</code>",
                "",
                "📝 <b>Example:</b>",
                "<code>/vipbackfill 123456789 14 \"Old grant\"</code>",
                "",
                "💡 Grants VIP + records history.",
                "   If user is already VIP, extends by `days`.",
            ]), parse_mode="HTML",
        )

    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    try:
        days = int(context.args[1])
        if days < 1 or days > 3650:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("⚠️ Days must be 1-3650.")

    reason = " ".join(context.args[2:])[:200]
    if not reason.strip():
        return await update.message.reply_text("⚠️ Reason required.")

    from services.vip import extend_vip_days
    new_exp = await extend_vip_days(
        target_id, days, reason=reason, source="backfill",
        granted_by=update.effective_user.id,
    )

    if not new_exp:
        return await update.message.reply_text(
            _sidebar("Failed", "❌", [
                "❌ VIP backfill failed.",
                "💡 Check DB / logs.",
            ]), parse_mode="HTML",
        )

    _log_action(
        update.effective_user.id,
        f"backfilled VIP {days}d for {target_id}: {reason[:50]}"
    )

    try:
        await log_event(
            context, "VIP_BACKFILL",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={
                "Days": days,
                "New expiry": new_exp.strftime('%d %b %Y'),
                "Reason": reason[:80],
            },
        )
    except Exception:
        pass

    await update.message.reply_text(
        _sidebar("Backfilled", "✅", [
            "✅ VIP record added + VIP granted",
            f"🎯 User : <code>{target_id}</code>",
            f"📦 Days : +{days}",
            f"⌛ Until : {new_exp.strftime('%d %b %Y')}",
            f"📝 Reason : {esc(reason)}",
            "",
            f"💡 Use <code>/vipinfo {target_id}</code> to verify",
        ]), parse_mode="HTML",
    )


async def cmd_invites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if users_collection is None:
        return await update.message.reply_text("⚠️ DB not connected.")

    if context.args:
        target_id, doc = await resolve_user(context.args[0])
        if not target_id:
            return await _not_found(update, context.args[0])

        total = await users_collection.count_documents({"referred_by": target_id})

        rewards_earned = 0
        try:
            from services.gifts import get_user_gifts
            gifts = await get_user_gifts(target_id, limit=100)
            rewards_earned = len([
                g for g in gifts
                if "referral" in str(g.get("reason", "")).lower()
            ])
        except Exception:
            pass

        from config import REFERRAL_REWARDS_NEEDED

        name = esc(doc.get("name") or "Unknown")
        uname = doc.get("username")
        uname_str = esc(f"@{uname}") if uname else "—"

        in_batch = total % REFERRAL_REWARDS_NEEDED
        remaining = (
            REFERRAL_REWARDS_NEEDED - in_batch
            if in_batch else REFERRAL_REWARDS_NEEDED
        )

        lines = [
            f"👤 User : <b>{name}</b>",
            f"🆔 ID : <code>{target_id}</code>",
            f"🔗 Username : {uname_str}",
            "",
            "👥  <b>Referral Stats</b>",
            f"  ├ Total invites : <b>{total}</b>",
            f"  ├ Rewards earned : <b>{rewards_earned} VIP days</b>",
            f"  ├ Progress : <b>{in_batch}/{REFERRAL_REWARDS_NEEDED}</b>",
            f"  └ Next reward in : <b>{remaining}</b> more",
        ]

        if total > 0:
            lines.append("")
            lines.append("📋  <b>Referred Users</b>")
            count = 0
            async for ref_doc in users_collection.find(
                {"referred_by": target_id},
                {"user_id": 1, "name": 1, "username": 1, "joined_date": 1},
            ).sort("joined_date", -1).limit(15):
                count += 1
                r_name = esc(ref_doc.get("name") or "—")
                r_uid = ref_doc.get("user_id")
                r_jd = ref_doc.get("joined_date")
                r_jd_str = r_jd.strftime("%d %b") if r_jd else "—"
                lines.append(f"  {count}. <code>{r_uid}</code> — {r_name} ({r_jd_str})")
            if total > 15:
                lines.append(f"  <i>…and {total - 15} more</i>")

        await _safe_reply(update, _sidebar("Referral Stats", "👥", lines))
        return

    pipeline = [
        {"$match": {"referred_by": {"$ne": None}}},
        {"$group": {"_id": "$referred_by", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]

    try:
        results = await users_collection.aggregate(pipeline).to_list(20)
    except Exception as e:
        logger.error(f"invites aggregate failed: {e}")
        return await update.message.reply_text("⚠️ Query failed.")

    if not results:
        return await update.message.reply_text(
            _sidebar("Leaderboard", "🏆", [
                "📭 No referrals yet.",
                "", "💡 Users can invite via /invite",
            ]), parse_mode="HTML",
        )

    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = [f"Top referrers : <b>{len(results)}</b>", ""]
    total_invites_all = 0

    for i, row in enumerate(results):
        uid_ = row["_id"]
        count = row["count"]
        total_invites_all += count
        medal = medals.get(i, f"{i+1}.")
        info = await users_collection.find_one({"user_id": uid_}, {"name": 1})
        name = esc((info or {}).get("name") or "—")
        lines.append(f"  {medal} <code>{uid_}</code> — {name}")
        lines.append(f"     └ <b>{count}</b> invites")

    lines.append("")
    lines.append(f"📊 Total invites (top 20) : <b>{total_invites_all}</b>")
    lines.append("💡 Use <code>/invites user_id</code> for details")

    await _safe_reply(update, _sidebar("Top Referrers", "🏆", lines))


async def cmd_chatdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if users_collection is None:
        return await update.message.reply_text(
            _sidebar("DB Down", "❌", ["⚠️ Database not connected."]),
            parse_mode="HTML",
        )

    lines = ["🔍 <b>Chat Persistence Diagnostic</b>", ""]

    active_uids = [uid for uid, u in users.items() if u.get("partner")]
    lines.append(f"🧠 Memory: <b>{len(active_uids)}</b> users with partner")
    lines.append("")

    matched = mismatched = missing = 0
    mismatched_details = []

    for uid in active_uids[:10]:
        u = users.get(uid)
        mem_partner = u.get("partner")
        mem_state = u.get("state")

        try:
            doc = await users_collection.find_one(
                {"user_id": uid},
                {"partner": 1, "state": 1}
            )

            if not doc:
                lines.append(f"❌ <code>{uid}</code>: NOT in DB")
                missing += 1
                continue

            db_partner = doc.get("partner")
            db_state = doc.get("state")

            if db_partner == mem_partner and db_state == "CHAT":
                lines.append(f"✅ <code>{uid}</code>: mem={mem_partner} db={db_partner}")
                matched += 1
            else:
                lines.append(
                    f"⚠️ <code>{uid}</code>: "
                    f"mem=<b>{mem_partner}</b> db=<b>{db_partner}</b> "
                    f"state=<b>{db_state}</b>"
                )
                mismatched += 1
                mismatched_details.append(
                    f"{uid}: mem={mem_partner} → db={db_partner} (state={db_state})"
                )
        except Exception as e:
            lines.append(f"❌ <code>{uid}</code>: ERR {str(e)[:40]}")

    lines.append("")
    lines.append("📊 <b>Summary</b>")
    lines.append(f"  ├ ✅ Matched : <b>{matched}</b>")
    lines.append(f"  ├ ⚠️ Mismatched : <b>{mismatched}</b>")
    lines.append(f"  └ ❌ Missing : <b>{missing}</b>")

    if mismatched > 0 or missing > 0:
        lines.append("")
        lines.append("🚨 <b>PROBLEM!</b> Partner NOT in DB")
        lines.append("💡 Check _do_save in database.py")
        if mismatched_details:
            lines.append("")
            lines.append("📋 <b>Mismatches:</b>")
            for d in mismatched_details[:5]:
                lines.append(f"  • <code>{esc(d)}</code>")
    else:
        lines.append("")
        lines.append("✅ <b>DB is perfect!</b>")

    await _safe_reply(update, _sidebar("Chat DB Diagnostic", "🔍", lines))


async def cmd_debug_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    uid = update.effective_user.id

    is_owner = (OWNER_ID and uid == OWNER_ID)
    in_cache = uid in admin_cache
    is_admin = await is_owner_or_admin(uid)

    lines = [
        "🔍 <b>Admin Debug Info</b>",
        "",
        "👤 <b>You</b>",
        f"  ├ ID : <code>{uid}</code>",
        f"  ├ Is OWNER_ID : {'✅ Yes' if is_owner else '❌ No'}",
        f"  ├ In admin_cache : {'✅ Yes' if in_cache else '❌ No'}",
        f"  └ Is owner_or_admin : {'✅ Yes' if is_admin else '❌ No'}",
        "",
        "⚙️ <b>Env Config</b>",
        f"  ├ OWNER_ID : <code>{OWNER_ID}</code>",
        f"  ├ ADMIN_IDS : <code>{ADMIN_IDS or 'empty'}</code>",
        f"  └ LOG_CHANNEL_ID : <code>{esc(LOG_CHANNEL_ID or 'empty')}</code>",
        "",
        "🧠 <b>admin_cache</b>",
        f"  ├ Size : <b>{len(admin_cache)}</b>",
    ]

    if admin_cache:
        cache_list = list(admin_cache)[:10]
        for cid in cache_list:
            marker = "👑" if cid == OWNER_ID else "🛡️"
            lines.append(f"  ├ {marker} <code>{cid}</code>")
        if len(admin_cache) > 10:
            lines.append(f"  └ ... +{len(admin_cache) - 10} more")
    else:
        lines.append("  └ ❌ <b>EMPTY</b> — THIS IS THE BUG!")

    u = users.get(uid)
    if u:
        lines.append("")
        lines.append("👤 <b>Your User Record</b>")
        lines.append(f"  ├ is_banned : {'🚫 Yes' if u.get('is_banned') else '✅ No'}")
        lines.append(f"  ├ is_admin : {'✅ Yes' if u.get('is_admin') else '❌ No'}")

    await update.message.reply_text(
        _sidebar("Admin Debug", "🔍", lines),
        parse_mode="HTML",
    )


async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent reports (unreviewed)."""
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    from services.moderation import (
        get_recent_reports, get_reports_for_user,
        mark_user_reports_reviewed,
    )

    # /reports review <id>
    if len(context.args) >= 2 and context.args[0].lower() == "review":
        target_id, doc = await resolve_user(context.args[1])
        if not target_id:
            return await _not_found(update, context.args[1])
        count = await mark_user_reports_reviewed(
            target_id, update.effective_user.id,
        )
        return await update.message.reply_text(
            _sidebar("Reports Reviewed", "✅", [
                f"✅ Marked <b>{count}</b> reports as reviewed",
                f"🎯 User : <code>{target_id}</code>",
            ]), parse_mode="HTML",
        )

    # /reports <id>
    if context.args:
        target_id, doc = await resolve_user(context.args[0])
        if not target_id:
            return await _not_found(update, context.args[0])
        reports = await get_reports_for_user(target_id, limit=30)
        if not reports:
            return await update.message.reply_text(
                _sidebar("Reports", "🚨", [
                    f"🎯 User : <code>{target_id}</code>",
                    "",
                    "📭 No reports for this user.",
                ]), parse_mode="HTML",
            )
        name = esc(doc.get("name") or "—")
        lines = [
            f"🎯 User : <b>{name}</b>",
            f"🆔 ID : <code>{target_id}</code>",
            f"📊 Total reports : <b>{len(reports)}</b>",
            "",
            "📋  <b>Recent Reports</b>",
        ]
        for r in reports[:20]:
            at = r.get("at")
            at_str = at.strftime("%d %b %H:%M") if at else "—"
            rep = r.get("reporter_id")
            reviewed = "✅" if r.get("reviewed") else "⏳"
            lines.append(f"  {reviewed} <code>{rep}</code> → {at_str}")
        if len(reports) > 20:
            lines.append(f"  <i>…and {len(reports) - 20} more</i>")
        lines.append("")
        lines.append(f"💡 /reports review {target_id} to clear")
        return await _safe_reply(
            update, _sidebar("Reports For User", "🚨", lines),
        )

    # /reports (list all)
    reports = await get_recent_reports(limit=30)
    if not reports:
        return await update.message.reply_text(
            _sidebar("Reports", "🚨", [
                "📭 No unreviewed reports.",
                "", "💡 All caught up!",
            ]), parse_mode="HTML",
        )

    from collections import Counter
    reported_counts = Counter(r["reported_id"] for r in reports)
    lines = [
        f"📊 Unreviewed : <b>{len(reports)}</b>",
        f"👥 Unique users : <b>{len(reported_counts)}</b>",
        "",
        "🔥  <b>Top Reported</b>",
    ]
    for reported_id, count in reported_counts.most_common(15):
        name = "—"
        u_mem = users.get(reported_id)
        if u_mem:
            name = esc(u_mem.get("name") or "—")
        elif users_collection is not None:
            try:
                d = await users_collection.find_one(
                    {"user_id": reported_id}, {"name": 1}
                )
                if d:
                    name = esc(d.get("name") or "—")
            except Exception:
                pass
        lines.append(f"  • <code>{reported_id}</code> {name} — <b>{count}</b>")
    lines.append("")
    lines.append("💡 /reports ID — detail")
    lines.append("💡 /reports review ID — clear")
    await _safe_reply(update, _sidebar("Recent Reports", "🚨", lines))


async def cmd_logtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test log channel — sends a sample event."""
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    chat_id = _resolve_log_channel()
    if not chat_id:
        return await update.message.reply_text(
            _sidebar("Log Channel", "📢", [
                "⚠️ <b>Not configured</b>",
                "",
                "Set <code>LOG_CHANNEL_ID</code> env var",
                "  ├ Format: <code>-1001234567890</code>",
                "  └ Or: <code>@channel_username</code>",
                "",
                "💡 Fallback: OWNER_ID DM",
            ]), parse_mode="HTML",
        )

    ok = await log_event(
        context, "BOT_START",
        category="system", level="info",
        actor_id=update.effective_user.id,
        details={
            "Test": "logtest command",
            "Channel": str(chat_id),
            "Config": LOG_CHANNEL_ID or "(empty — OWNER fallback)",
        },
    )

    if ok:
        await update.message.reply_text(
            _sidebar("Log Test Sent", "✅", [
                f"✅ Test event sent to: <code>{chat_id}</code>",
                "",
                "Check the channel now.",
            ]), parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            _sidebar("Log Test Failed", "❌", [
                "❌ Could not send to channel.",
                "",
                "💡 Check:",
                "  ├ Bot is admin in channel?",
                "  ├ Channel ID correct?",
                "  └ See logs for details",
            ]), parse_mode="HTML",
        )


# ══════════════════════════════════════════════════════════════
# ⭐ /resetmatching — Emergency full reset of matching subsystem
# ══════════════════════════════════════════════════════════════
async def cmd_resetmatching(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Completely reset the matching subsystem — queue, states.

    Usage: /resetmatching
    Admin only. Safe to run anytime.
    """
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    admin_id = update.effective_user.id
    progress = await update.message.reply_text(
        _sidebar("Resetting Matching", "🔄", [
            "⏳ Clearing queue and states...",
            "⚠️ <i>Do not interrupt.</i>",
        ]), parse_mode="HTML"
    )

    from state import last_next_time, pending_media_timeouts, active_voice_rooms
    from database import _do_save
    from services.matching import disconnect

    stats = {
        "queue_cleared": 0,
        "users_idled": 0,
        "chats_disconnected": 0,
        "db_synced": 0,
        "errors": [],
    }

    # ── STEP 1: Disconnect all active chats ──
    try:
        active_pairs = []
        seen = set()
        for uid, u in list(users.items()):
            if u.get("state") == "CHAT" and u.get("partner"):
                pid = u["partner"]
                pair_key = tuple(sorted([uid, pid]))
                if pair_key not in seen:
                    seen.add(pair_key)
                    active_pairs.append((uid, pid))

        for a, b in active_pairs:
            try:
                await disconnect(context, a, b, ender_id=admin_id)
                stats["chats_disconnected"] += 1
            except Exception as e:
                stats["errors"].append(f"disconnect ({a},{b}): {e}")
    except Exception as e:
        stats["errors"].append(f"disconnect_all: {e}")

    # ── STEP 2: Clear queue ──
    try:
        async with queue_lock:
            stats["queue_cleared"] = len(queue)
            queue.clear()
            queue_set.clear()
    except Exception as e:
        stats["errors"].append(f"queue_clear: {e}")

    # ── STEP 3: Reset every user to IDLE + DB sync ──
    try:
        for uid, u in list(users.items()):
            if u.get("state") in ("SEARCHING", "MATCHING", "CHAT") or u.get("partner"):
                u["state"] = "IDLE"
                u["partner"] = None
                u["pending_media"] = {}
                u["_search_msg_id"] = None
                u["awaiting_input"] = None
                u["_pending_save_partner_id"] = None
                try:
                    ok = await _do_save(uid, u, pop_queue=True)
                    if ok:
                        stats["db_synced"] += 1
                except Exception as e:
                    stats["errors"].append(f"save {uid}: {e}")
                stats["users_idled"] += 1
    except Exception as e:
        stats["errors"].append(f"users_reset: {e}")

    # ── STEP 4: Clear timestamps + pending ──
    try:
        last_next_time.clear()
    except Exception:
        pass
    try:
        pending_media_timeouts.clear()
    except Exception:
        pass
    try:
        active_voice_rooms.clear()
    except Exception:
        pass

    # ── STEP 5: Log ──
    try:
        await log_event(
            context, "RESET_MATCHING",
            category="admin", level="critical",
            actor_id=admin_id,
            details={
                "Queue cleared": stats["queue_cleared"],
                "Chats disconnected": stats["chats_disconnected"],
                "Users reset": stats["users_idled"],
                "DB synced": stats["db_synced"],
                "Errors": len(stats["errors"]),
            },
        )
    except Exception:
        pass

    _log_action(
        admin_id,
        f"RESET MATCHING: queue={stats['queue_cleared']}, users={stats['users_idled']}"
    )
    logger.warning(
        f"🔄 Admin {admin_id} reset matching — "
        f"queue={stats['queue_cleared']}, "
        f"chats={stats['chats_disconnected']}, users={stats['users_idled']}, "
        f"db_synced={stats['db_synced']}"
    )

    # ── Final report ──
    lines = [
        f"✅ Queue cleared : <b>{stats['queue_cleared']}</b>",
        f"✅ Chats disconnected : <b>{stats['chats_disconnected']}</b>",
        f"✅ Users reset to IDLE : <b>{stats['users_idled']}</b>",
        f"✅ DB synced : <b>{stats['db_synced']}</b>",
    ]
    if stats["errors"]:
        lines.append("")
        lines.append(f"⚠️ Errors : <b>{len(stats['errors'])}</b>")
        for err in stats["errors"][:3]:
            lines.append(f"  • <code>{esc(str(err)[:80])}</code>")

    lines.append("")
    lines.append("💡 <b>What to do now:</b>")
    lines.append("  1. Have users send /next again")
    lines.append("  2. Test with 2 accounts")
    lines.append("  3. If still stuck → check logs")

    try:
        await progress.edit_text(
            _sidebar("Matching Reset Done", "✅", lines),
            parse_mode="HTML",
        )
    except Exception:
        await update.message.reply_text(
            _sidebar("Matching Reset Done", "✅", lines),
            parse_mode="HTML",
        )