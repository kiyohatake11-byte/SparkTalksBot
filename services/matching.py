import asyncio
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import NEXT_COOLDOWN_SECONDS, MAX_RECENT_PARTNERS, MAX_BLOCKED_USERS
from state import users, queue, queue_lock, last_next_time
from database import get_user, save_user_to_db
from utils import spark_card, safe_send, utcnow, to_bold
from keyboards import get_main_keyboard, get_chat_keyboard

logger = logging.getLogger("sparktalks")


async def disconnect(context, u1: int, u2: int, requeue: bool = False):
    for uid in (u1, u2):
        u = users.get(uid)
        if not u:
            continue
        partner = u.get("partner")
        if partner:
            recent = u.setdefault("recent_partners", [])
            if partner not in recent:
                recent.append(partner)
                if len(recent) > MAX_RECENT_PARTNERS:
                    recent.pop(0)
        u["partner"] = None
        u["state"] = "IDLE"
        u["pending_media"] = {}

    ended_card = (
        f"❖ <b>{to_bold('Chat Ended')}</b>\n"
        f"───────────────────────────────\n\n"
        f"🔒 The conversation has been closed.\n\n"
        f"<i>Ready to meet someone new?</i>\n\n"
        f"💡 Tap <b>Find Partner</b> or send /next to start again."
    )

    await safe_send(context, u1, spark_card("Chat Ended", ended_card, "SparkTalks"),
                    parse_mode="HTML", reply_markup=get_main_keyboard())
    await safe_send(context, u2, spark_card("Chat Ended", ended_card, "SparkTalks"),
                    parse_mode="HTML", reply_markup=get_main_keyboard())

    if requeue:
        u = users.get(u1)
        if u:
            u["state"] = "SEARCHING"
            async with queue_lock:
                if u1 not in queue:
                    queue.append(u1)
            name = u.get("name") or "there"
            body = (
                f"⏳ Hey {name}, looking for someone new...\n\n"
                f"<i>Sit tight, matching you with a fresh partner.</i>"
            )
            await safe_send(
                context, u1,
                spark_card("Searching", body, "Hang tight"),
                parse_mode="HTML"
            )
            await try_match(context, u1)


async def connect_users(context, uid1: int, uid2: int):
    """Helper to connect two users and send match messages"""
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return

    u1["partner"] = uid2
    u1["state"] = "CHAT"
    u1["pending_media"] = {}

    u2["partner"] = uid1
    u2["state"] = "CHAT"
    u2["pending_media"] = {}

    connected_body = (
        f"✨ <b>{to_bold('Woohoo! You are Connected')}</b> ✨\n\n"
        f"❖ <b>{to_bold('Session Info')}</b>\n"
        f"  🔒 Privacy  : Encrypted\n"
        f"  🎭 Identity : Anonymous\n"
        f"  🟢 Status   : Active\n\n"
        f"❖ <b>{to_bold('Getting Started')}</b>\n"
        f"  💡 <i>Say Hi or ask a fun question to begin!</i>\n\n"
        f"❖ <b>{to_bold('Quick Controls')}</b>\n"
        f"  🔄 /next — New Partner\n"
        f"  🛑 /end  — End Chat"
    )

    actions = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Next", callback_data="CHAT_NEXT"),
         InlineKeyboardButton("🛑 End", callback_data="CHAT_END")],
        [InlineKeyboardButton("🚨 Report", callback_data="CHAT_REPORT"),
         InlineKeyboardButton("🚫 Block", callback_data="CHAT_BLOCK")]
    ])

    await safe_send(context, uid1, spark_card("Connected", connected_body, "SparkTalks"),
                    reply_markup=actions, parse_mode="HTML")
    await context.bot.send_message(chat_id=uid1, text="Chat controls:", reply_markup=get_chat_keyboard())

    await safe_send(context, uid2, spark_card("Connected", connected_body, "SparkTalks"),
                    reply_markup=actions, parse_mode="HTML")
    await context.bot.send_message(chat_id=uid2, text="Chat controls:", reply_markup=get_chat_keyboard())


async def try_match(context, uid: int):
    u = await get_user(uid)
    if not u or u.get("is_banned"):
        return

    name = u.get("name") or "there"

    if not u.get("gender"):
        return await safe_send(
            context, uid,
            spark_card("Setup Required", f"⚠️ Hey {name}, please run /start first."),
            parse_mode="HTML"
        )

    now_ts = utcnow().timestamp()
    last = last_next_time.get(uid, 0)
    if now_ts - last < NEXT_COOLDOWN_SECONDS:
        remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last))
        return await safe_send(
            context, uid,
            spark_card("Please Wait", f"⏳ Hey {name}, wait <b>{remaining}s</b> before searching again."),
            parse_mode="HTML"
        )
    last_next_time[uid] = now_ts

    if u.get("partner"):
        return await disconnect(context, uid, u["partner"], requeue=True)

    async with queue_lock:
        if u.get("state") == "SEARCHING" and uid in queue:
            return await safe_send(
                context, uid,
                spark_card("Already Searching", f"🔍 Hey {name}, you're already in the queue..."),
                parse_mode="HTML"
            )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"
        await save_user_to_db(uid, u)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")]
        ])
        body = (
            f"⚠️ <b>{to_bold('Gender filter is VIP only')}</b>\n\n"
            f"<i>Preference reset to <b>Any</b>.</i>\n\n"
            f"Upgrade to VIP to unlock gender filtering."
        )
        return await safe_send(
            context, uid,
            spark_card("VIP Needed", body, "SparkTalks"),
            reply_markup=kb, parse_mode="HTML"
        )

    recent = set(u.get("recent_partners", []))
    blocked_by_me = set(u.get("blocked_users", []))
    found = None

    async with queue_lock:
        for candidate_id in list(queue):
            if candidate_id == uid:
                continue
            c = users.get(candidate_id)
            if not c or c.get("is_banned"):
                continue
            if candidate_id in recent or candidate_id in blocked_by_me:
                continue
            if uid in set(c.get("blocked_users", [])):
                continue

            cond1 = u["pref_gender"] == "Any" or c.get("gender") == u["pref_gender"]
            cond2 = c.get("pref_gender") == "Any" or u.get("gender") == c.get("pref_gender")
            if cond1 and cond2:
                found = candidate_id
                break

        if found is not None:
            try:
                queue.remove(found)
            except ValueError:
                pass
            try:
                queue.remove(uid)
            except ValueError:
                pass

            await connect_users(context, uid, found)
            return

        u["state"] = "SEARCHING"
        if uid not in queue:
            queue.append(uid)
        waiting = len(queue)

    body = (
        f"⏳ Hey {name}, looking for someone to chat with...\n\n"
        f"❖ <b>{to_bold('Queue')}</b>\n"
        f"  👥 People waiting: <b>{waiting}</b>"
    )
    await safe_send(
        context, uid,
        spark_card("Searching", body, "Hang tight"),
        parse_mode="HTML"
    )

    # Immediate extra match attempt
    asyncio.create_task(background_matcher(context))


async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    """Fast background matcher - runs every 2 seconds"""
    async with queue_lock:
        if len(queue) < 2:
            return
        waiting = list(queue)

    matched = set()

    for i, uid1 in enumerate(waiting):
        if uid1 in matched:
            continue

        u1 = users.get(uid1)
        if not u1 or u1.get("state") != "SEARCHING" or u1.get("partner") or u1.get("is_banned"):
            continue

        for uid2 in waiting[i+1:]:
            if uid2 in matched:
                continue

            u2 = users.get(uid2)
            if not u2 or u2.get("state") != "SEARCHING" or u2.get("partner") or u2.get("is_banned"):
                continue

            # Skip recent & blocked
            if uid2 in set(u1.get("recent_partners", [])) or uid1 in set(u2.get("recent_partners", [])):
                continue
            if uid2 in set(u1.get("blocked_users", [])) or uid1 in set(u2.get("blocked_users", [])):
                continue

            # Gender check
            cond1 = u1.get("pref_gender", "Any") == "Any" or u2.get("gender") == u1.get("pref_gender")
            cond2 = u2.get("pref_gender", "Any") == "Any" or u1.get("gender") == u2.get("pref_gender")

            if cond1 and cond2:
                matched.add(uid1)
                matched.add(uid2)

                async with queue_lock:
                    try:
                        queue.remove(uid1)
                    except ValueError:
                        pass
                    try:
                        queue.remove(uid2)
                    except ValueError:
                        pass

                await connect_users(context, uid1, uid2)
                break


async def report_internal(context, uid: int):
    from state import admin_cache
    u = await get_user(uid)
    if not u or not u.get("partner"):
        return await safe_send(
            context, uid,
            spark_card("Error", "⚠️ You are not in an active chat."),
            parse_mode="HTML"
        )
    partner_id = u["partner"]

    body = (
        f"❖ <b>{to_bold('Report Details')}</b>\n"
        f"  👤 Reporter: <code>{uid}</code>\n"
        f"  🎯 Reported: <code>{partner_id}</code>\n"
        f"  ⏰ {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    report = spark_card("Report Received", body, "Moderation")

    for admin_id in admin_cache:
        if admin_id:
            await safe_send(context, admin_id, report, parse_mode="HTML")

    await safe_send(
        context, uid,
        spark_card("Report Sent", "✅ Report sent to the team.\n\n<i>Thank you for helping keep SparkTalks safe.</i>"),
        parse_mode="HTML"
    )


async def block_internal(context, uid: int):
    u = await get_user(uid)
    if not u or not u.get("partner"):
        return await safe_send(
            context, uid,
            spark_card("Error", "⚠️ You are not in an active chat."),
            parse_mode="HTML"
        )
    partner_id = u["partner"]
    blocked = u.setdefault("blocked_users", [])
    if partner_id not in blocked:
        blocked.append(partner_id)
        if len(blocked) > MAX_BLOCKED_USERS:
            u["blocked_users"] = blocked[-MAX_BLOCKED_USERS:]
    await save_user_to_db(uid, u)
    await disconnect(context, uid, partner_id, requeue=True)


async def end_chat_internal(context, uid: int):
    u = await get_user(uid)
    name = u.get("name") if u else "there"
    if not u or (not u.get("partner") and u.get("state") != "SEARCHING"):
        return await safe_send(
            context, uid,
            spark_card("Notice", f"⚠️ Hey {name}, you are not in a chat or search."),
            parse_mode="HTML", reply_markup=get_main_keyboard()
        )
    if u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
        u["state"] = "IDLE"
        return await safe_send(
            context, uid,
            spark_card("Search Cancelled", f"🛑 Hey {name}, search stopped."),
            parse_mode="HTML", reply_markup=get_main_keyboard()
        )
    await disconnect(context, uid, u["partner"])