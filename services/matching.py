import asyncio
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import NEXT_COOLDOWN_SECONDS, MAX_RECENT_PARTNERS, MAX_BLOCKED_USERS
from state import users, queue, queue_lock, last_next_time
from database import get_user, save_user_to_db
from utils import box_card, box_simple, safe_send, utcnow, to_bold, to_serif_bold
from keyboards import get_main_keyboard, get_chat_keyboard

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# HELPER: Check if two users can match
# ══════════════════════════════════════════════════════════════
def can_match(u1: dict, u2: dict) -> bool:
    """Check if two users are compatible for matching."""
    if not u1 or not u2:
        return False
    if u1.get("is_banned") or u2.get("is_banned"):
        return False
    if u1.get("partner") or u2.get("partner"):
        return False
    if u1.get("state") != "SEARCHING" or u2.get("state") != "SEARCHING":
        return False
    if u1.get("user_id") == u2.get("user_id"):
        return False

    # Blocked check
    blocked_1 = set(u1.get("blocked_users", []))
    blocked_2 = set(u2.get("blocked_users", []))
    uid1 = u1.get("user_id")
    uid2 = u2.get("user_id")
    if uid2 in blocked_1 or uid1 in blocked_2:
        return False

    # Recent partners check
    recent_1 = set(u1.get("recent_partners", []))
    recent_2 = set(u2.get("recent_partners", []))
    if uid2 in recent_1 or uid1 in recent_2:
        return False

    # Gender check
    g1 = u1.get("gender")
    g2 = u2.get("gender")
    p1 = u1.get("pref_gender", "Any")
    p2 = u2.get("pref_gender", "Any")

    # Condition 1: u1 wants u2's gender
    cond1 = (p1 == "Any") or (p1 == g2)
    # Condition 2: u2 wants u1's gender
    cond2 = (p2 == "Any") or (p2 == g1)

    return cond1 and cond2


# ══════════════════════════════════════════════════════════════
# DISCONNECT: End chat between two users
# ══════════════════════════════════════════════════════════════
async def disconnect(context, u1: int, u2: int, requeue: bool = False):
    """Disconnect two users from a chat."""
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

    ended_blocks = [
        {"type": "text", "content": "The conversation has been closed."},
        {"type": "divider"},
        {"type": "text", "content": "Ready to meet someone new?"},
        {"type": "divider"},
        {"type": "quote", "content": "Tap Find Partner or send /next to start again."},
    ]
    ended_card = box_card("Chat Ended", ended_blocks, emoji="🛑")

    await safe_send(context, u1, ended_card, parse_mode="HTML", reply_markup=get_main_keyboard())
    await safe_send(context, u2, ended_card, parse_mode="HTML", reply_markup=get_main_keyboard())

    if requeue:
        u = users.get(u1)
        if u:
            u["state"] = "SEARCHING"
            async with queue_lock:
                if u1 not in queue:
                    queue.append(u1)
            name = u.get("name") or "there"
            body = box_card(
                "Searching",
                [
                    {"type": "text", "content": f"Hey {name}, looking for someone new..."},
                    {"type": "divider"},
                    {"type": "text", "content": "⏳ Sit tight, matching you with a fresh partner."},
                ],
                emoji="🔍"
            )
            await safe_send(context, u1, body, parse_mode="HTML")
            # ✅ Recursion ke bajaye seedha return
            return


# ══════════════════════════════════════════════════════════════
# CONNECT: Match two users
# ══════════════════════════════════════════════════════════════
async def connect_users(context, uid1: int, uid2: int):
    """Connect two users and send match messages."""
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        logger.error(f"[CONNECT] User not found: {uid1}={bool(u1)}, {uid2}={bool(u2)}")
        return False

    # ✅ CRITICAL FIX: user_id set karo (agar nahi hai)
    u1["user_id"] = uid1
    u2["user_id"] = uid2

    u1["partner"] = uid2
    u1["state"] = "CHAT"
    u1["pending_media"] = {}

    u2["partner"] = uid1
    u2["state"] = "CHAT"
    u2["pending_media"] = {}

    connected_body = (
        f"✨ {to_bold('Woohoo! You are Connected')} ✨\n\n"
        f"❖ {to_serif_bold('Session Info')}\n"
        f"  🔒 Privacy  : Encrypted\n"
        f"  🎭 Identity : Anonymous\n"
        f"  🟢 Status   : Active\n\n"
        f"❖ {to_serif_bold('Getting Started')}\n"
        f"  💡 Say Hi or ask a fun question to begin!\n\n"
        f"❖ {to_serif_bold('Quick Controls')}\n"
        f"  🔄 /next — New Partner\n"
        f"  🛑 /end  — End Chat"
    )

    # ─── User 1 inline buttons ───
    row2_buttons_1 = [InlineKeyboardButton("🚨 Report", callback_data="CHAT_REPORT")]
    if u1.get("is_vip"):
        row2_buttons_1.append(InlineKeyboardButton("🚫 Block", callback_data="CHAT_BLOCK"))
    actions1 = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Next", callback_data="CHAT_NEXT"),
         InlineKeyboardButton("🛑 End", callback_data="CHAT_END")],
        row2_buttons_1
    ])

    # ─── User 2 inline buttons ───
    row2_buttons_2 = [InlineKeyboardButton("🚨 Report", callback_data="CHAT_REPORT")]
    if u2.get("is_vip"):
        row2_buttons_2.append(InlineKeyboardButton("🚫 Block", callback_data="CHAT_BLOCK"))
    actions2 = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Next", callback_data="CHAT_NEXT"),
         InlineKeyboardButton("🛑 End", callback_data="CHAT_END")],
        row2_buttons_2
    ])

    # ─── Send to User 1 ───
    try:
        await safe_send(context, uid1, box_card("Connected", connected_body, emoji="✨"),
                        reply_markup=actions1, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=uid1,
            text="Chat controls:",
            reply_markup=get_chat_keyboard(u1)
        )
    except Exception as e:
        logger.error(f"[CONNECT] Failed to send to {uid1}: {e}")

    # ─── Send to User 2 ───
    try:
        await safe_send(context, uid2, box_card("Connected", connected_body, emoji="✨"),
                        reply_markup=actions2, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=uid2,
            text="Chat controls:",
            reply_markup=get_chat_keyboard(u2)
        )
    except Exception as e:
        logger.error(f"[CONNECT] Failed to send to {uid2}: {e}")

    logger.info(f"[CONNECT] ✅ Connected {uid1} ↔ {uid2}")
    return True


# ══════════════════════════════════════════════════════════════
# TRY_MATCH: User wants to find a partner
# ══════════════════════════════════════════════════════════════
async def try_match(context, uid: int):
    u = await get_user(uid)
    if not u or u.get("is_banned"):
        return

    # ✅ CRITICAL FIX: user_id set karo
    u["user_id"] = uid

    name = u.get("name") or "there"

    # ─── Setup check ───
    if not u.get("gender"):
        return await safe_send(
            context, uid,
            box_simple("Setup Required", f"⚠️ Hey {name}, please run /start first.", emoji="⚠️"),
            parse_mode="HTML"
        )

    # ─── Cooldown check ───
    now_ts = utcnow().timestamp()
    last = last_next_time.get(uid, 0)
    if now_ts - last < NEXT_COOLDOWN_SECONDS:
        remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last))
        return await safe_send(
            context, uid,
            box_simple("Please Wait", f"⏳ Hey {name}, wait {remaining}s before searching again.", emoji="⏳"),
            parse_mode="HTML"
        )
    last_next_time[uid] = now_ts

    # ─── Agar user already kisi chat me hai ───
    if u.get("partner"):
        await disconnect(context, uid, u["partner"], requeue=False)
        # ab u ka partner None hai, aage badho

    # ─── Agar user already searching hai ───
    async with queue_lock:
        if u.get("state") == "SEARCHING" and uid in queue:
            return await safe_send(
                context, uid,
                box_simple("Already Searching", f"🔍 Hey {name}, you're already in the queue...", emoji="🔍"),
                parse_mode="HTML"
            )

    # ─── VIP check for gender filter ───
    if not u.get("is_vip") and u.get("pref_gender") not in ("Any", None):
        u["pref_gender"] = "Any"
        await save_user_to_db(uid, u)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")]
        ])
        body = box_card(
            "VIP Needed",
            [
                {"type": "text", "content": "⚠️ Gender filter is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content": "Preference reset to Any."},
            ],
            emoji="👑"
        )
        return await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")

    # ─── Ensure defaults ───
    if not u.get("pref_gender"):
        u["pref_gender"] = "Any"

    # ✅ CRITICAL FIX: Direct scan + match
    found_id = None
    async with queue_lock:
        queue_snapshot = list(queue)
        logger.info(f"[TRY_MATCH] User {uid} ({u.get('gender')}/{u.get('pref_gender')}) scanning {len(queue_snapshot)} users: {queue_snapshot}")

        for candidate_id in queue_snapshot:
            if candidate_id == uid:
                continue
            c = users.get(candidate_id)
            if not c:
                logger.info(f"[TRY_MATCH] Candidate {candidate_id} not in memory — skip")
                continue

            # ✅ Ensure candidate has user_id
            c["user_id"] = candidate_id

            if can_match(u, c):
                found_id = candidate_id
                logger.info(f"[TRY_MATCH] ✅ MATCH FOUND: {uid} ↔ {candidate_id}")
                break
            else:
                logger.info(f"[TRY_MATCH] Candidate {candidate_id} not compatible")

        # ─── Match mil gaya ───
        if found_id is not None:
            try:
                queue.remove(found_id)
            except ValueError:
                pass
            try:
                queue.remove(uid)
            except ValueError:
                pass

            u["state"] = "CHAT"
            c = users.get(found_id)
            if c:
                c["state"] = "CHAT"

            await connect_users(context, uid, found_id)
            return

        # ─── Match nahi mila — queue me add karo ───
        u["state"] = "SEARCHING"
        if uid not in queue:
            queue.append(uid)
        waiting = len(queue)

    logger.info(f"[TRY_MATCH] User {uid} added to queue. Size={waiting}")

    body = box_card(
        "Searching",
        [
            {"type": "text", "content": f"Hey {name}, looking for someone to chat with..."},
            {"type": "divider"},
            {"type": "kv", "items": [(f"👥 {to_bold('Waiting')}", str(waiting))]},
        ],
        emoji="🔍"
    )
    await safe_send(context, uid, body, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# BACKGROUND_MATCHER: Runs every 2 seconds
# ══════════════════════════════════════════════════════════════
async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    """Background matcher — runs periodically."""
    async with queue_lock:
        if len(queue) < 2:
            return
        waiting = list(queue)

    logger.info(f"[BG_MATCH] Queue: {waiting}")

    matched = set()

    for i, uid1 in enumerate(waiting):
        if uid1 in matched:
            continue
        u1 = users.get(uid1)
        if not u1:
            continue
        u1["user_id"] = uid1

        for uid2 in waiting[i+1:]:
            if uid2 in matched:
                continue
            u2 = users.get(uid2)
            if not u2:
                continue
            u2["user_id"] = uid2

            if can_match(u1, u2):
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

                u1["state"] = "CHAT"
                u2["state"] = "CHAT"

                logger.info(f"[BG_MATCH] ✅ Matching {uid1} ↔ {uid2}")
                await connect_users(context, uid1, uid2)
                break


# ══════════════════════════════════════════════════════════════
# REPORT: Report current partner
# ══════════════════════════════════════════════════════════════
async def report_internal(context, uid: int):
    from state import admin_cache
    u = await get_user(uid)
    if not u or not u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ You are not in an active chat.", emoji="⚠️"),
            parse_mode="HTML"
        )
    partner_id = u["partner"]

    body = box_card(
        "Report Received",
        [
            {"type": "kv", "items": [
                (f"👤 {to_bold('Reporter')}", f"<code>{uid}</code>"),
                (f"🎯 {to_bold('Reported')}", f"<code>{partner_id}</code>"),
            ]},
            {"type": "divider"},
            {"type": "text", "content": f"⏰ {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"},
        ],
        emoji="🚨"
    )

    for admin_id in admin_cache:
        if admin_id:
            await safe_send(context, admin_id, body, parse_mode="HTML")

    await safe_send(
        context, uid,
        box_simple("Report Sent", "✅ Report sent to the team.\n\nThank you for helping keep SparkTalks safe.", emoji="✅"),
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════
# BLOCK: Block current partner (VIP only)
# ══════════════════════════════════════════════════════════════
async def block_internal(context, uid: int):
    u = await get_user(uid)
    if not u:
        return

    # ✅ VIP check
    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
            [InlineKeyboardButton("🚨 Report Instead", callback_data="CHAT_REPORT")]
        ])
        body = box_card(
            "VIP Required",
            [
                {"type": "text", "content": "⚠️ Block feature is available for VIP members only."},
                {"type": "divider"},
                {"type": "section", "emoji": "👑", "heading": "Why VIP?"},
                {"type": "line", "content": "• Block unwanted users instantly"},
                {"type": "line", "content": "• Never match with them again"},
                {"type": "line", "content": "• Priority matching"},
                {"type": "line", "content": "• Gender filter"},
                {"type": "divider"},
                {"type": "text", "content": "💡 You can still /report inappropriate users for free."},
            ],
            emoji="👑"
        )
        return await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")

    # ─── VIP user — normal block flow ───
    if not u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ You are not in an active chat.", emoji="⚠️"),
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


# ══════════════════════════════════════════════════════════════
# END CHAT
# ══════════════════════════════════════════════════════════════
async def end_chat_internal(context, uid: int):
    u = await get_user(uid)
    name = u.get("name") if u else "there"
    if not u or (not u.get("partner") and u.get("state") != "SEARCHING"):
        return await safe_send(
            context, uid,
            box_simple("Notice", f"⚠️ Hey {name}, you are not in a chat or search.", emoji="⚠️"),
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
            box_simple("Search Cancelled", f"🛑 Hey {name}, search stopped.", emoji="🛑"),
            parse_mode="HTML", reply_markup=get_main_keyboard()
        )
    await disconnect(context, uid, u["partner"])