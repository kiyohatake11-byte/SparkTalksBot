"""Communication: broadcasts, DM, notify, scheduling."""
import logging
from datetime import timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from state import scheduled_broadcasts, broadcast_state
from database import is_owner_or_admin, resolve_user
from utils import safe_send, utcnow, esc
from handlers.admin._helpers import (
    _sidebar, _log_action, _deny, _not_found,
)

logger = logging.getLogger("sparktalks")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    parts = (update.message.text or "").split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/broadcast message</code>",
                "",
                "💡 '🎲 Find Partner' button auto-attach hoga.",
            ]),
            parse_mode="HTML",
        )
    message = parts[1].strip()
    context.user_data["pending_broadcast"] = message
    preview = _sidebar("Confirm Broadcast", "📢", [
        "📢 Preview:", "", esc(message), "",
        "⚠️ Will be sent to ALL users (excluding banned).",
        "🔘 Button: 🎲 Find Partner",
    ])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send", callback_data="BC_CONFIRM"),
        InlineKeyboardButton("❌ Cancel", callback_data="BC_CANCEL"),
    ]])
    await update.message.reply_text(preview, reply_markup=kb, parse_mode="HTML")


async def cmd_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not update.message.reply_to_message:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Reply to a message to broadcast it.",
                "",
                "💡 '🎲 Find Partner' button auto-attach hoga.",
            ]),
            parse_mode="HTML",
        )

    src = update.message.reply_to_message
    from utils import ikb as _ikb
    kb_markup = InlineKeyboardMarkup([[
        _ikb("🎲 Find Partner", callback_data="START_NEXT", style="primary"),
    ]])

    await update.message.reply_text("📢 Broadcasting media...")

    from services.broadcast import execute_media_broadcast
    sent, failed = await execute_media_broadcast(
        context,
        src.chat_id,
        src.message_id,
        admin_uid=update.effective_user.id,
        reply_markup=kb_markup,
    )
    await update.message.reply_text(
        _sidebar("Broadcast Done", "📢",
                 [f"✅ Sent: {sent}", f"❌ Failed: {failed}"]),
        parse_mode="HTML",
    )


async def cmd_stopbroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not broadcast_state.get("running"):
        return await update.message.reply_text(
            _sidebar("Not Running", "ℹ️", [
                "No broadcast currently running.",
                "",
                "💡 Start one with /broadcast or /broadcast_media",
            ]),
            parse_mode="HTML",
        )
    broadcast_state["stop_requested"] = True
    broadcast_state["paused"] = False
    _log_action(update.effective_user.id, "requested broadcast stop")
    await update.message.reply_text(
        _sidebar("Stop Requested", "🛑", [
            "🛑 Broadcast will stop after current batch.",
            "",
            "📊 <b>Current Progress</b>",
            f"  ├ Processed : <b>{broadcast_state['processed']}/{broadcast_state['total']}</b>",
            f"  ├ ✅ Sent : <b>{broadcast_state['sent']}</b>",
            f"  └ ❌ Failed : <b>{broadcast_state['failed']}</b>",
            "",
            "💡 Auto-stop in ~1-2 sec",
            "⚠️ For instant stop use /cancelbroadcast",
        ]),
        parse_mode="HTML",
    )


async def cmd_pausebroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not broadcast_state.get("running"):
        return await update.message.reply_text(
            _sidebar("Not Running", "ℹ️", ["No broadcast currently running."]),
            parse_mode="HTML",
        )
    if broadcast_state.get("paused"):
        return await update.message.reply_text(
            _sidebar("Already Paused", "⏸", [
                "Broadcast is already paused.",
                "", "💡 Use /resumebroadcast to continue",
            ]),
            parse_mode="HTML",
        )
    broadcast_state["paused"] = True
    _log_action(update.effective_user.id, "paused broadcast")
    await update.message.reply_text(
        _sidebar("Paused", "⏸", [
            "⏸ Broadcast paused.",
            "",
            "📊 <b>Current Progress</b>",
            f"  ├ Processed : <b>{broadcast_state['processed']}/{broadcast_state['total']}</b>",
            f"  ├ ✅ Sent : <b>{broadcast_state['sent']}</b>",
            f"  └ ❌ Failed : <b>{broadcast_state['failed']}</b>",
            "",
            "💡 /resumebroadcast to continue",
        ]),
        parse_mode="HTML",
    )


async def cmd_resumebroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not broadcast_state.get("running"):
        return await update.message.reply_text(
            _sidebar("Not Running", "ℹ️", ["No broadcast currently running."]),
            parse_mode="HTML",
        )
    if not broadcast_state.get("paused"):
        return await update.message.reply_text(
            _sidebar("Not Paused", "▶️", ["Broadcast is not paused."]),
            parse_mode="HTML",
        )
    broadcast_state["paused"] = False
    _log_action(update.effective_user.id, "resumed broadcast")
    await update.message.reply_text(
        _sidebar("Resumed", "▶️", [
            "▶️ Broadcast resumed!",
            "",
            "📊 <b>Current Progress</b>",
            f"  ├ Processed : <b>{broadcast_state['processed']}/{broadcast_state['total']}</b>",
            f"  ├ ✅ Sent : <b>{broadcast_state['sent']}</b>",
            f"  └ ❌ Failed : <b>{broadcast_state['failed']}</b>",
        ]),
        parse_mode="HTML",
    )


async def cmd_cancelbroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not broadcast_state.get("running"):
        return await update.message.reply_text(
            _sidebar("Not Running", "ℹ️", [
                "No broadcast currently running.",
            ]),
            parse_mode="HTML",
        )
    broadcast_state["cancel_requested"] = True
    broadcast_state["paused"] = False
    broadcast_state["stop_requested"] = True
    _log_action(update.effective_user.id, "cancelled broadcast")
    await update.message.reply_text(
        _sidebar("Broadcast Cancelled", "🚫", [
            "🚫 <b>Broadcast cancelled immediately!</b>",
            "",
            "📊 <b>Final Progress</b>",
            f"  ├ Processed : <b>{broadcast_state['processed']}/{broadcast_state['total']}</b>",
            f"  ├ ✅ Sent : <b>{broadcast_state['sent']}</b>",
            f"  └ ❌ Failed : <b>{broadcast_state['failed']}</b>",
            "",
            "⚡ In-flight sends (~5-10) may still deliver.",
        ]),
        parse_mode="HTML",
    )


async def cmd_broadcaststatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    if not broadcast_state.get("running"):
        return await update.message.reply_text(
            _sidebar("No Broadcast", "📢", [
                "📢 No broadcast currently running.",
                "", "💡 Start one:",
                "  ├ /broadcast message",
                "  └ /broadcast_media (reply to message)",
            ]),
            parse_mode="HTML",
        )

    processed = broadcast_state["processed"]
    total = broadcast_state["total"]
    sent = broadcast_state["sent"]
    failed = broadcast_state["failed"]
    btype = broadcast_state.get("type") or "unknown"
    started = broadcast_state.get("started_at")

    percent = int((processed / total) * 100) if total > 0 else 0
    bar_filled = percent // 5
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    elapsed = "—"
    rate = 0
    eta = "—"
    if started:
        try:
            secs = (utcnow() - started).total_seconds()
            elapsed = str(timedelta(seconds=int(secs)))
            if secs > 0:
                rate = processed / secs
                remaining = total - processed
                if rate > 0:
                    eta_secs = remaining / rate
                    eta = str(timedelta(seconds=int(eta_secs)))
        except Exception:
            pass

    if broadcast_state.get("cancel_requested"):
        status_icon = "🚫 Cancelling..."
    elif broadcast_state.get("paused"):
        status_icon = "⏸ Paused"
    elif broadcast_state.get("stop_requested"):
        status_icon = "🛑 Stopping..."
    else:
        status_icon = "🟢 Running"

    await update.message.reply_text(
        _sidebar("Broadcast Status", "📢", [
            f"⚡ Type : <b>{btype.title()}</b>",
            f"🎯 Status : <b>{status_icon}</b>",
            "",
            "📊 <b>Progress</b>",
            f"  ├ <code>{bar}</code> {percent}%",
            f"  ├ Processed : <b>{processed}/{total}</b>",
            f"  ├ ✅ Sent : <b>{sent}</b>",
            f"  └ ❌ Failed : <b>{failed}</b>",
            "",
            "⏱ <b>Timing</b>",
            f"  ├ Elapsed : {elapsed}",
            f"  ├ Rate : <b>{rate:.1f}/s</b>",
            f"  └ ETA : {eta}",
        ]),
        parse_mode="HTML",
    )


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /dm ID message")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    message = esc(" ".join(context.args[1:]))
    result = await safe_send(
        context, target_id,
        _sidebar("Message", "📩", [message, "", "— SparkTalks Team"]),
        parse_mode="HTML",
    )
    if result:
        await update.message.reply_text(
            _sidebar("Sent", "✅", [f"✅ Delivered to <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            _sidebar("Failed", "❌", ["❌ Could not deliver."]), parse_mode="HTML"
        )


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /notify ID message")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    message = esc(" ".join(context.args[1:]))
    await safe_send(context, target_id, f"🔔 <i>{message}</i>", parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Sent", "🔔", [f"✅ Notified <code>{target_id}</code>"]),
        parse_mode="HTML",
    )


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Schedule Broadcast", "⏰", [
                "Usage:",
                "<code>/schedule minutes message</code>",
                "Or reply to a message:",
                "<code>/schedule minutes</code>",
            ]), parse_mode="HTML",
        )
    try:
        minutes = int(context.args[0])
        if minutes < 1 or minutes > 60 * 24 * 7:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("⚠️ Minutes must be 1–10080 (7 days).")

    from services.scheduler import schedule_broadcast
    run_at = utcnow() + timedelta(minutes=minutes)

    if update.message.reply_to_message:
        src = update.message.reply_to_message
        if src.chat_id != update.effective_user.id:
            return await update.message.reply_text(
                _sidebar("Error", "⚠️", [
                    "⚠️ For media, reply in your DM with the bot.",
                    "", "💡 Group/channel replies cannot be scheduled.",
                ]),
                parse_mode="HTML",
            )
        sid = await schedule_broadcast(
            run_at, media={"chat_id": src.chat_id, "message_id": src.message_id},
            created_by=update.effective_user.id,
        )
    else:
        text = " ".join(context.args[1:])
        if not text.strip():
            return await update.message.reply_text("⚠️ No message provided.")
        sid = await schedule_broadcast(
            run_at, message=text, created_by=update.effective_user.id,
        )

    if not sid:
        return await update.message.reply_text(
            _sidebar("Failed", "❌", [
                "❌ Could not schedule broadcast.",
                "⚠️ Database may be unreachable.",
                "", "💡 Try again in a moment.",
            ]),
            parse_mode="HTML",
        )

    await update.message.reply_text(
        _sidebar("Scheduled", "⏰", [
            "✅ Scheduled broadcast",
            f"⏰ Runs at: {run_at.strftime('%d %b %H:%M')} UTC",
            f"🆔 ID: <code>{sid}</code>",
        ]), parse_mode="HTML",
    )


async def cmd_scheduled_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not scheduled_broadcasts:
        return await update.message.reply_text("📭 No scheduled broadcasts.")
    lines = [f"Total: {len(scheduled_broadcasts)}", ""]
    for s in scheduled_broadcasts:
        preview = esc((s.get("message") or "[media]")[:40])
        lines.append(f"• <code>{s['id']}</code>")
        lines.append(f"  ⏰ {s['run_at'].strftime('%d %b %H:%M')} — {preview}")
    await update.message.reply_text(
        _sidebar("Scheduled", "⏰", lines), parse_mode="HTML"
    )


async def cmd_scheduled_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /unschedule ID")
    from services.scheduler import cancel_scheduled
    ok = await cancel_scheduled(context.args[0])
    if ok:
        await update.message.reply_text("✅ Cancelled.")
    else:
        await update.message.reply_text("⚠️ Not found.")