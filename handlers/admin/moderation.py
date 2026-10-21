"""Moderation: ban, mute, kick, blocks.

⭐ FIXED:
  - Bug #11: ban/kick delete stale searching cards
  - Bug #19: all direct writes re-queued via save_user_to_db
⭐ NEW:
  - Every action logged to LOG_CHANNEL
"""
import logging
from datetime import timedelta
from telegram import Update
from telegram.ext import ContextTypes

from state import (
    users, queue, queue_set, queue_lock, admin_cache, muted_users,
)
from database import (
    is_owner_or_admin, users_collection, resolve_user, save_user_to_db,
)
from utils import safe_send, utcnow, esc, log_event
from services.matching import disconnect, _delete_searching_card
from handlers.admin._helpers import (
    _sidebar, _safe_reply, _log_action, _deny, _require_db, _not_found,
)

logger = logging.getLogger("sparktalks")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /ban ID [reason]")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"

    try:
        await _delete_searching_card(context, target_id)
    except Exception:
        pass

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_banned": True, "banned_reason": reason,
                  "banned_at": utcnow(),
                  "banned_by": update.effective_user.id,
                  "state": "IDLE", "partner": None}},
    )
    admin_cache.discard(target_id)
    u = users.get(target_id)
    if u:
        u["is_banned"] = True
        u["banned_reason"] = reason
        u["banned_at"] = utcnow()
        u["banned_by"] = update.effective_user.id
        if u.get("partner"):
            await disconnect(context, target_id, u["partner"], ender_id=target_id)
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
        u["partner"] = None
        await save_user_to_db(target_id, u)

    await safe_send(context, target_id,
                    _sidebar("Banned", "🚫", [f"🚫 You are banned! {esc(reason)}"]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Banned <code>{target_id}</code>"]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"banned {target_id}: {reason}")

    try:
        await log_event(
            context, "BAN",
            category="admin", level="critical",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={"Reason": reason},
        )
    except Exception:
        pass


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /unban ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_banned": False, "banned_reason": None, "warnings": []}},
    )
    u = users.get(target_id)
    if u:
        u["is_banned"] = False
        u["warnings"] = []
        await save_user_to_db(target_id, u)
    if doc.get("is_admin"):
        admin_cache.add(target_id)
    await safe_send(context, target_id,
                    _sidebar("Unbanned", "✅", ["✅ Unbanned. Welcome back!"]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅",
                 [f"✅ <code>{target_id}</code> unbanned (warnings cleared)."]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "UNBAN",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /kick ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    u = users.get(target_id)
    if not u:
        return await update.message.reply_text("User offline.")

    kicked_from = "idle"
    try:
        await _delete_searching_card(context, target_id)
    except Exception:
        pass

    if u.get("partner"):
        await disconnect(context, target_id, u["partner"], ender_id=target_id)
        kicked_from = "chat"
    elif u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
        kicked_from = "search"
        await save_user_to_db(target_id, u)

    await safe_send(context, target_id,
                    _sidebar("Kicked", "👢", ["👢 Kicked by admin."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅",
                 [f"✅ Kicked <code>{target_id}</code> from {kicked_from}"]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "KICK",
            category="admin", level="warn",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={"From": kicked_from},
        )
    except Exception:
        pass


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /mute ID [minutes]")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    try:
        minutes = int(context.args[1]) if len(context.args) > 1 else 10
    except ValueError:
        minutes = 10
    unmute_at = utcnow() + timedelta(minutes=minutes)
    muted_users[target_id] = unmute_at.timestamp()
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"muted_until": unmute_at}},
    )
    u = users.get(target_id)
    if u:
        u["muted_until"] = unmute_at
        await save_user_to_db(target_id, u)
    await safe_send(context, target_id,
                    _sidebar("Muted", "🔇", [f"🔇 Muted for {minutes} min."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Muted <code>{target_id}</code> for {minutes}m"]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "MUTE",
            category="admin", level="warn",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={"Duration": f"{minutes} min"},
        )
    except Exception:
        pass


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /unmute ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    muted_users.pop(target_id, None)
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"muted_until": None}},
    )
    u = users.get(target_id)
    if u:
        u["muted_until"] = None
        await save_user_to_db(target_id, u)
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Unmuted <code>{target_id}</code>"]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "UNMUTE",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_mutelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    now_ts = utcnow().timestamp()
    active_memory = {
        uid: unmute_ts
        for uid, unmute_ts in muted_users.items()
        if unmute_ts > now_ts
    }

    db_mutes = []
    if users_collection is not None:
        try:
            cursor = users_collection.find(
                {"muted_until": {"$gt": utcnow()}},
                {"user_id": 1, "name": 1, "username": 1, "muted_until": 1},
            )
            async for doc in cursor:
                db_mutes.append(doc)
        except Exception as e:
            logger.error(f"mutelist DB query failed: {e}")

    if not active_memory and not db_mutes:
        return await update.message.reply_text(
            _sidebar("Muted Users", "🔇", [
                "✅ No muted users currently.",
                "",
                "💡 Mute a user: <code>/mute ID [minutes]</code>",
            ]),
            parse_mode="HTML",
        )

    lines = []

    if active_memory:
        lines.append(f"🔇 <b>Active Mutes</b> ({len(active_memory)})")
        lines.append("")
        sorted_memory = sorted(
            active_memory.items(), key=lambda x: x[1], reverse=True,
        )
        for uid, unmute_ts in sorted_memory[:30]:
            remaining = int(unmute_ts - now_ts)
            mins = remaining // 60
            secs = remaining % 60
            time_str = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m {secs}s"
            mem_u = users.get(uid)
            if mem_u:
                name = esc(mem_u.get("name") or "—")
                uname = mem_u.get("username")
                uname_str = esc(f"@{uname}") if uname else ""
            else:
                try:
                    _, doc = await resolve_user(str(uid))
                    if doc:
                        name = esc(doc.get("name") or "—")
                        uname = doc.get("username")
                        uname_str = esc(f"@{uname}") if uname else ""
                    else:
                        name = "—"
                        uname_str = ""
                except Exception:
                    name = "—"
                    uname_str = ""
            line = f"• <code>{uid}</code> — {name}"
            if uname_str:
                line += f" ({uname_str})"
            lines.append(line)
            lines.append(f"  └ ⏱ <b>{time_str}</b> remaining")
        if len(active_memory) > 30:
            lines.append(f"  <i>…and {len(active_memory) - 30} more</i>")

    memory_uids = set(active_memory.keys())
    db_only = [d for d in db_mutes if d.get("user_id") not in memory_uids]

    if db_only:
        if lines:
            lines.append("")
        lines.append(f"💾 <b>DB Mutes</b> ({len(db_only)})")
        lines.append("<i>(persisted, may not be enforced after restart)</i>")
        lines.append("")
        for doc in db_only[:20]:
            uid = doc.get("user_id")
            name = esc(doc.get("name") or "—")
            uname = doc.get("username")
            uname_str = esc(f"@{uname}") if uname else ""
            muted_until = doc.get("muted_until")
            if muted_until:
                remaining = int((muted_until - utcnow()).total_seconds())
                if remaining > 0:
                    mins = remaining // 60
                    secs = remaining % 60
                    time_str = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m {secs}s"
                else:
                    time_str = "expired"
            else:
                time_str = "—"
            line = f"• <code>{uid}</code> — {name}"
            if uname_str:
                line += f" ({uname_str})"
            lines.append(line)
            lines.append(f"  └ ⏱ {time_str}")
        if len(db_only) > 20:
            lines.append(f"  <i>…and {len(db_only) - 20} more</i>")

    lines.append("")
    lines.append("📊 <b>Summary</b>")
    lines.append(f"  ├ Active (memory) : <b>{len(active_memory)}</b>")
    lines.append(f"  └ DB-only : <b>{len(db_only)}</b>")

    await _safe_reply(
        update,
        _sidebar("Muted Users", "🔇", lines,
                 tip="Use <code>/unmute ID</code> to unmute a user"),
    )


async def cmd_unmuteall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    now_ts = utcnow().timestamp()
    memory_count = sum(1 for ts in muted_users.values() if ts > now_ts)
    muted_users.clear()

    db_count = 0
    if users_collection is not None:
        try:
            result = await users_collection.update_many(
                {"muted_until": {"$ne": None}},
                {"$set": {"muted_until": None}},
            )
            db_count = result.modified_count
        except Exception as e:
            logger.error(f"unmuteall DB update failed: {e}")
            return await update.message.reply_text(
                _sidebar("Error", "❌", [
                    "⚠️ Failed to clear DB mutes.",
                    f"📝 Error : <code>{esc(str(e)[:200])}</code>",
                    "",
                    "💡 Memory cleared, but DB still has stale data.",
                ]),
                parse_mode="HTML",
            )

    try:
        from state import link_warn_count
        link_warn_count.clear()
    except Exception:
        pass

    _log_action(
        update.effective_user.id,
        f"unmuted ALL users (memory={memory_count}, db={db_count})"
    )

    try:
        await log_event(
            context, "UNMUTEALL",
            category="admin", level="warn",
            actor_id=update.effective_user.id,
            details={
                "Memory mutes cleared": memory_count,
                "DB records cleared": db_count,
            },
        )
    except Exception:
        pass

    logger.warning(
        f"🔊 Bulk unmute by admin {update.effective_user.id}: "
        f"memory={memory_count}, db={db_count}"
    )

    await update.message.reply_text(
        _sidebar("Unmuted All", "🔊", [
            "✅ <b>All users unmuted!</b>",
            "",
            "📊 <b>Summary</b>",
            f"  ├ 🧠 Memory : <b>{memory_count}</b> users",
            f"  ├ 💾 Database : <b>{db_count}</b> records",
            f"  └ ⚠️ Anti-link warns : <b>cleared</b>",
            "",
            "💡 <i>All users can chat again immediately.</i>",
        ], tip="Use /mutelist to verify — should be empty"),
        parse_mode="HTML",
    )


async def cmd_forceend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /forceend ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    u = users.get(target_id)
    if not u:
        return await update.message.reply_text("User offline.")
    if u.get("state") == "SEARCHING":
        try:
            await _delete_searching_card(context, target_id)
        except Exception:
            pass
        async with queue_lock:
            try:
                queue.remove(target_id)
            except ValueError:
                pass
            queue_set.discard(target_id)
        u["state"] = "IDLE"
        await save_user_to_db(target_id, u)

        try:
            await log_event(
                context, "KICK",
                category="admin", level="info",
                actor_id=update.effective_user.id,
                target_id=target_id,
                details={"From": "search queue"},
            )
        except Exception:
            pass

        return await update.message.reply_text(
            _sidebar("Done", "✅", [f"✅ Search cancelled for <code>{target_id}</code>"]),
            parse_mode="HTML",
        )
    partner = u.get("partner")
    if not partner:
        return await update.message.reply_text("Not in a chat.")
    await disconnect(context, target_id, partner, ender_id=target_id)

    try:
        await log_event(
            context, "KICK",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={"From": f"chat with {partner}"},
        )
    except Exception:
        pass

    await update.message.reply_text(
        _sidebar("Done", "✅",
                 [f"✅ Ended <code>{target_id}</code> & <code>{partner}</code>"]),
        parse_mode="HTML",
    )


async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = []
    if users_collection is not None:
        async for doc in users_collection.find(
            {"is_banned": True},
            {"user_id": 1, "name": 1, "username": 1, "banned_reason": 1},
        ).limit(30):
            name = esc(doc.get("name") or "—")
            uname = esc(f"@{doc['username']}" if doc.get("username") else "—")
            reason = esc(doc.get("banned_reason") or "—")
            lines.append(f"• <code>{doc['user_id']}</code> {name} ({uname})")
            lines.append(f"  └ {reason}")
    if not lines:
        lines = ["✅ No banned users."]
    await _safe_reply(update, _sidebar("Ban List", "🚫", lines))


async def cmd_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /blocked ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    blocked_ids = doc.get("blocked_users", []) or []
    if not blocked_ids:
        return await update.message.reply_text(
            f"✅ No blocks for <code>{target_id}</code>.", parse_mode="HTML"
        )
    lines = [f"Total blocked: {len(blocked_ids)}"]
    if users_collection is not None:
        async for b in users_collection.find(
            {"user_id": {"$in": blocked_ids[:50]}},
            {"user_id": 1, "name": 1, "username": 1},
        ):
            name = esc(b.get("name") or "Unknown")
            uname = esc(f"@{b.get('username')}" if b.get("username") else "—")
            lines.append(f"• <code>{b['user_id']}</code> {name} ({uname})")
    await _safe_reply(update, _sidebar("Blocked Users", "🚫", lines))


async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /unblock ID blocked_id")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    blocked_id, _ = await resolve_user(context.args[1])
    if not blocked_id:
        return await _not_found(update, context.args[1])
    blocked = doc.get("blocked_users", []) or []
    if blocked_id not in blocked:
        return await update.message.reply_text("⚠️ Not in block list.")
    new_list = [b for b in blocked if b != blocked_id]
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"blocked_users": new_list}}
    )
    u = users.get(target_id)
    if u:
        u["blocked_users"] = new_list
        await save_user_to_db(target_id, u)
    await update.message.reply_text(
        _sidebar("Unblocked", "🔓", [f"✅ Unblocked <code>{blocked_id}</code>"]),
        parse_mode="HTML",
    )


async def cmd_clearblocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /clearblocks ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    count = len(doc.get("blocked_users", []) or [])
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"blocked_users": []}}
    )
    u = users.get(target_id)
    if u:
        u["blocked_users"] = []
        await save_user_to_db(target_id, u)
    await update.message.reply_text(
        _sidebar("Cleared", "🔓", [f"✅ Cleared {count} blocks for <code>{target_id}</code>"]),
        parse_mode="HTML",
    )