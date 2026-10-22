"""User management: VIP, verify, warnings, profile reset.

⭐ FIXED:
  - Bug #6: /warn auto-ban disconnects + clears queue + deletes searching card
  - Bug #7: /resetprofile resets state/partner in DB
  - Bug #19: All direct writes follow with save_user_to_db()
⭐ NEW:
  - Every action logged to LOG_CHANNEL
"""
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from config import OWNER_ID, VIP_PLANS
from state import (
    users, admin_cache, queue, queue_set, queue_lock,
)
from database import (
    is_owner_or_admin, users_collection, resolve_user,
    save_user_to_db,
)
from utils import safe_send, utcnow, esc, log_event
from services.vip import activate_vip
from services.matching import disconnect, _delete_searching_card
from handlers.admin._helpers import (
    _sidebar, _log_action, _deny, _require_db, _not_found,
)

logger = logging.getLogger("sparktalks")


async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if len(context.args) < 2:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", [
                "Usage: <code>/addvip ID|@user plan</code>",
                "Plans: PLAN_14D, PLAN_1M, PLAN_3M, PLAN_6M",
            ]), parse_mode="HTML",
        )
    target_id, _ = await resolve_user(context.args[0])
    plan_key = context.args[1].upper()
    if not target_id:
        return await _not_found(update, context.args[0])
    if plan_key not in VIP_PLANS:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Invalid plan key."]), parse_mode="HTML"
        )

    plan = VIP_PLANS[plan_key]
    new_exp = await activate_vip(
        target_id, plan_key,
        source="admin",
        granted_by=update.effective_user.id,
        note="Admin grant",
    )

    if not new_exp:
        return await update.message.reply_text(
            _sidebar("Failed", "❌", [
                "❌ VIP activation failed!",
                f"🎯 User : <code>{target_id}</code>",
                f"📦 Plan : {esc(plan['name'])}",
                "", "💡 Check DB / logs.",
            ]), parse_mode="HTML",
        )

    if OWNER_ID:
        try:
            target_u = await resolve_user(str(target_id))
            doc_t = target_u[1] or {}
            name_t = esc(doc_t.get("name") or "Unknown")
            uname_t = doc_t.get("username")
            uname_str_t = esc(f"@{uname_t}") if uname_t else "—"
        except Exception:
            name_t = "Unknown"
            uname_str_t = "—"

        notify_body = (
            f"🎁  ✨  <b>VIP Granted by Admin</b>  ✨  🎁\n"
            f"▎\n"
            f"▎ 👤 User : <b>{name_t}</b>\n"
            f"▎ 🆔 ID : <code>{target_id}</code>\n"
            f"▎ 🔗 Username : {uname_str_t}\n"
            f"▎\n"
            f"▎ 🎁 Plan : <b>{esc(plan['name'])}</b>\n"
            f"▎ 📦 Days : <b>+{plan['days']}</b>\n"
            f"▎ ⌛ Until : <b>{new_exp.strftime('%d %b %Y')}</b>\n"
            f"▎ 🛡️ Admin : <code>{update.effective_user.id}</code>\n"
            f"▎\n"
            f"▎ 🕐 {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        await safe_send(context, OWNER_ID, notify_body, parse_mode="HTML")

    await safe_send(context, target_id, _sidebar("VIP Activated", "🎉", [
        "🎉 Your VIP is now active!",
        f"🌟 {esc(plan['name'])}",
        f"⌛ Until : {new_exp.strftime('%d %b %Y')}",
    ], "Enjoy!"), parse_mode="HTML")

    await update.message.reply_text(_sidebar("Success", "✅", [
        f"✅ Granted <b>{esc(plan['name'])}</b>",
        f"🎯 User : <code>{target_id}</code>",
    ]), parse_mode="HTML")
    _log_action(update.effective_user.id, f"granted VIP {plan_key} to {target_id}")

    try:
        await log_event(
            context, "VIP_GRANT",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={
                "Plan": plan["name"],
                "Days": plan["days"],
                "Expires": new_exp.strftime('%d %b %Y'),
            },
        )
    except Exception:
        pass


async def cmd_removevip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/removevip ID</code>"]),
            parse_mode="HTML",
        )
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"is_vip": False, "vip_expiry_date": None,
                  "vip_tier_name": "None", "pref_gender": "Any"}},
    )
    u = users.get(target_id)
    if u:
        u["is_vip"] = False
        u["vip_expiry_date"] = None
        u["vip_tier_name"] = "None"
        u["pref_gender"] = "Any"
        await save_user_to_db(target_id, u)
    await safe_send(context, target_id,
                    _sidebar("VIP Removed", "⌛", ["⌛ VIP removed."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ VIP removed from <code>{target_id}</code>."]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"removed VIP from {target_id}")

    try:
        await log_event(
            context, "VIP_REMOVE",
            category="admin", level="warn",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/verify ID|@user</code>"]),
            parse_mode="HTML",
        )
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"verified": True}}
    )
    u = users.get(target_id)
    if u:
        u["verified"] = True
        await save_user_to_db(target_id, u)
    await safe_send(
        context, target_id,
        "✅  ✨  <b>Account Verified</b>  ✨  ✅\n"
        "▎\n▎ 🎉 You now have a verified badge.\n"
        "▎\n💡 <i>Your /profile will show a ✅ mark.</i>",
        parse_mode="HTML",
    )
    await update.message.reply_text(
        _sidebar("Verified", "✅", [f"✅ <code>{target_id}</code> is now verified."]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "VERIFY",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_unverify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /unverify ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"verified": False}}
    )
    u = users.get(target_id)
    if u:
        u["verified"] = False
        await save_user_to_db(target_id, u)
    await update.message.reply_text(
        _sidebar("Unverified", "✅", [f"✅ <code>{target_id}</code> unverified."]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "UNVERIFY",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text(
            _sidebar("Error", "⚠️", ["Usage: <code>/warn ID [reason]</code>"]),
            parse_mode="HTML",
        )
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    warnings.append({"reason": reason, "by": update.effective_user.id,
                     "at": utcnow().isoformat()})

    if len(warnings) >= 3:
        await users_collection.update_one(
            {"user_id": target_id},
            {"$set": {"warnings": warnings, "is_banned": True,
                      "banned_reason": f"Auto-ban: {len(warnings)} warnings",
                      "banned_at": utcnow(),
                      "banned_by": update.effective_user.id,
                      "state": "IDLE", "partner": None}},
        )
        u = users.get(target_id)
        if u:
            u["warnings"] = warnings
            u["is_banned"] = True
            u["banned_reason"] = f"Auto-ban: {len(warnings)} warnings"
            u["banned_at"] = utcnow()
            u["banned_by"] = update.effective_user.id

            if u.get("partner"):
                try:
                    await disconnect(
                        context, target_id, u["partner"], ender_id=target_id,
                    )
                except Exception as e:
                    logger.error(
                        f"Auto-ban disconnect failed for {target_id}: {e}"
                    )
                    u["partner"] = None
                    u["state"] = "IDLE"

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
            u["partner"] = None
            u["pending_media"] = {}
            await save_user_to_db(target_id, u)

        await safe_send(
            context, target_id,
            _sidebar("Auto-Banned", "🚫", [
                f"🚫 You are banned! {len(warnings)} warnings.",
            ]),
            parse_mode="HTML",
        )
        _log_action(
            update.effective_user.id,
            f"auto-banned {target_id} ({len(warnings)} warnings)",
        )

        try:
            await log_event(
                context, "BAN",
                category="admin", level="critical",
                actor_id=update.effective_user.id,
                target_id=target_id,
                details={
                    "Reason": f"Auto-ban: {len(warnings)} warnings",
                    "Via": "/warn",
                },
            )
        except Exception:
            pass

        return await update.message.reply_text(
            _sidebar("Auto-Banned", "🚫", [
                f"⚠️ {len(warnings)}th warning → auto-banned <code>{target_id}</code>",
            ]),
            parse_mode="HTML",
        )

    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"warnings": warnings}}
    )
    u = users.get(target_id)
    if u:
        u["warnings"] = warnings
        await save_user_to_db(target_id, u)
    await safe_send(context, target_id,
                    _sidebar("Warning", "⚠️", [
                        f"⚠️ Warning #{len(warnings)}",
                        f"📝 {esc(reason)}",
                    ], "3 warnings = ban!"), parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Warned", "⚠️",
                 [f"⚠️ Warning #{len(warnings)} → <code>{target_id}</code>"]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "WARN",
            category="admin", level="warn",
            actor_id=update.effective_user.id,
            target_id=target_id,
            details={
                "Warning": f"{len(warnings)}/3",
                "Reason": reason,
            },
        )
    except Exception:
        pass


async def cmd_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /warnings ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list) or not warnings:
        return await update.message.reply_text(_sidebar(
            "Warnings", "✅", [f"<code>{target_id}</code>: no warnings."]
        ), parse_mode="HTML")
    lines = [f"Total : {len(warnings)}/3"]
    for i, w in enumerate(warnings):
        lines.append(f"#{i+1} — {esc(w.get('reason', '—'))}")
    await update.message.reply_text(_sidebar("Warnings", "⚠️", lines), parse_mode="HTML")


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /unwarn ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    warnings = doc.get("warnings", [])
    if not isinstance(warnings, list) or not warnings:
        return await update.message.reply_text("No warnings to remove.")
    warnings.pop()
    await users_collection.update_one(
        {"user_id": target_id}, {"$set": {"warnings": warnings}}
    )
    u = users.get(target_id)
    if u:
        u["warnings"] = warnings
        await save_user_to_db(target_id, u)
    await update.message.reply_text(_sidebar(
        "Removed", "✅", [f"✅ Last warning removed from <code>{target_id}</code>."]
    ), parse_mode="HTML")


async def cmd_resetprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /resetprofile ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])

    u = users.get(target_id)
    if u and u.get("partner"):
        try:
            await disconnect(context, target_id, u["partner"], ender_id=target_id)
        except Exception as e:
            logger.error(f"resetprofile disconnect failed for {target_id}: {e}")
    try:
        await _delete_searching_card(context, target_id)
    except Exception:
        pass

    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {
            "gender": None, "age": None, "country": None, "bio": None,
            "interests": [], "pref_gender": "Any",
            "profile_public": False, "confirm_media": True,
            "state": "IDLE", "partner": None,
        }},
    )

    u = users.get(target_id)
    if u:
        for k in ["gender", "age", "country", "bio"]:
            u[k] = None
        u["interests"] = []
        u["pref_gender"] = "Any"
        u["profile_public"] = False
        u["confirm_media"] = True
        u["state"] = "IDLE"
        u["partner"] = None
        u["pending_media"] = {}
        await save_user_to_db(target_id, u)

    async with queue_lock:
        try:
            queue.remove(target_id)
        except ValueError:
            pass
        queue_set.discard(target_id)

    await safe_send(context, target_id,
                    _sidebar("Profile Reset", "🔄", ["🔄 Profile reset. Run /start."]),
                    parse_mode="HTML")
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Profile reset for <code>{target_id}</code>."]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "RESETPROFILE",
            category="admin", level="warn",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /resetstats ID")
    target_id, _ = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    await users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"total_chats": 0, "total_matches": 0, "report_count": 0}},
    )
    u = users.get(target_id)
    if u:
        u["total_chats"] = 0
        u["total_matches"] = 0
        u["report_count"] = 0
        await save_user_to_db(target_id, u)
    await update.message.reply_text(
        _sidebar("Success", "✅", [f"✅ Stats reset for <code>{target_id}</code>."]),
        parse_mode="HTML",
    )

    try:
        await log_event(
            context, "RESETSTATS",
            category="admin", level="info",
            actor_id=update.effective_user.id,
            target_id=target_id,
        )
    except Exception:
        pass


async def cmd_whois(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /whois ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    mem = users.get(target_id, {})
    name = esc(doc.get("name") or "—")
    uname = esc(f"@{doc.get('username')}" if doc.get("username") else "—")
    status = f"👑 {esc(doc.get('vip_tier_name', 'VIP'))}" if doc.get("is_vip") else "⚪ Free"
    warnings = doc.get("warnings", [])
    w_count = len(warnings) if isinstance(warnings, list) else 0
    state_str = "🟢 In Chat" if mem.get("partner") else (
        "🟡 Searching" if mem.get("state") == "SEARCHING" else "⚪ Idle"
    )
    await update.message.reply_text(_sidebar(
        "Quick Lookup", "🔍", [
            f"👤 <b>{name}</b> ({uname})",
            f"🆔 <code>{target_id}</code>",
            f"{status}",
            f"💬 Chats : {doc.get('total_chats', 0)}",
            f"🏆 Matches : {doc.get('total_matches', 0)}",
            f"⚠️ Warnings : {w_count}/3",
            f"🚫 Banned : {'Yes' if doc.get('is_banned') else 'No'}",
            f"🟢 Now : {state_str}",
        ]
    ), parse_mode="HTML")


async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not context.args:
        return await update.message.reply_text("Usage: /userinfo ID")
    target_id, doc = await resolve_user(context.args[0])
    if not target_id:
        return await _not_found(update, context.args[0])
    mem = users.get(target_id, {})
    exp = doc.get("vip_expiry_date")
    exp_str = exp.strftime("%d %b %Y") if exp else "—"
    name = esc(doc.get("name") or "Unknown")
    uname = esc(f"@{doc.get('username')}" if doc.get("username") else "—")
    status = f"👑 {esc(doc.get('vip_tier_name', 'VIP'))}" if doc.get("is_vip") else "⚪ Free"
    warnings = doc.get("warnings", [])
    w_count = len(warnings) if isinstance(warnings, list) else 0
    blocked = doc.get("blocked_users", []) or []
    state_line = "🟢 In Chat" if mem.get("partner") else (
        "🟡 Searching" if mem.get("state") == "SEARCHING" else "⚪ Idle"
    )
    # ⭐ NEW: Read matching fields with fallback
    pref_gender_db = doc.get("pref_gender", "Any")
    pref_gender_mem = mem.get("pref_gender", "Any") if mem else "Any"
    pending_credit = mem.get("pending_pref_from_credit", False) if mem else False
    credits = doc.get("gender_pref_credits", 0)

    # Warn if pref mismatched between memory and DB
    pref_mismatch = ""
    if mem and pref_gender_db != pref_gender_mem:
        pref_mismatch = f" ⚠️ (mem={pref_gender_mem})"

    # Warn if non-VIP has non-Any pref — this is the bug!
    stale_warning = ""
    if not doc.get("is_vip") and pref_gender_db not in ("Any", None, ""):
        stale_warning = "\n  └ 🚨 <b>STALE PREF — NON-VIP!</b>"

    lines = [
        "🆔  <b>Identity</b>",
        f"  ├ <code>{target_id}</code>",
        f"  ├ {name} | {uname}",
        f"  ├ {doc.get('gender') or '—'} | 🎂 {doc.get('age') or '—'}",
        f"  ├ 🌍 {esc(doc.get('country') or '—')}",
        f"  └ 🏷️ {esc(', '.join(doc.get('interests') or []) or 'None')}",
        "",
        "🔍  <b>Matching</b>",
        f"  ├ 🚻 Your Gender : <b>{doc.get('gender') or '—'}</b>",
        f"  ├ 🎯 Pref (DB) : <b>{pref_gender_db}</b>{pref_mismatch}",
        f"  ├ 🎯 Pref (Mem) : <b>{pref_gender_mem}</b>",
        f"  ├ 🎁 Credits : <b>{credits}</b>",
        f"  └ ⚡ Pending Credit Pref : <b>{'Yes' if pending_credit else 'No'}</b>{stale_warning}",
        "",
        "⭐  <b>Status</b>",
        f"  ├ {status}",
        f"  ├ ⌛ {exp_str}",
        f"  ├ 🚫 Banned: {'Yes' if doc.get('is_banned') else 'No'}",
        f"  ├ ⚠️ Warnings: {w_count}/3",
        f"  ├ ✅ Verified: {'Yes' if doc.get('verified') else 'No'}",
        f"  └ 🛡️ Admin: {'Yes' if doc.get('is_admin') else 'No'}",
        "",
        "📊  <b>Activity</b>",
        f"  ├ 💬 Chats: {doc.get('total_chats', 0)}",
        f"  ├ 🏆 Matches: {doc.get('total_matches', 0)}",
        f"  ├ 🚫 Blocked: {len(blocked)}",
        f"  └ 🚨 Reports: {doc.get('report_count', 0)}",
        "",
        "⚡  <b>Runtime</b>",
        f"  ├ State: {state_line}",
        f"  └ Partner: {mem.get('partner') or 'None'}",
    ]
    await update.message.reply_text(_sidebar("User Info", "👤", lines), parse_mode="HTML")