import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    NEXT_COOLDOWN_SECONDS,
    MAX_RECENT_PARTNERS,
    MAX_BLOCKED_USERS,
    ALLOW_INSTANT_REMATCH,
    BLOCK_REQUIRES_VIP,
)
from state import users, queue, queue_set, queue_lock, last_next_time, admin_cache
from database import get_user, save_user_to_db
from utils import box_card, box_simple, safe_send, utcnow, to_bold
from keyboards import get_main_keyboard, get_chat_keyboard

logger = logging.getLogger("sparktalks")


# ──────────────────────────────────────────────────────────────
# HELPER: Recent partner + Blocked user checks
# ──────────────────────────────────────────────────────────────

def _is_recent(u: dict, candidate_id: int) -> bool:
    if ALLOW_INSTANT_REMATCH or MAX_RECENT_PARTNERS <= 0:
        return False
    return candidate_id in set(u.get("recent_partners", []))


def _is_blocked(u: dict, candidate_id: int) -> bool:
    return candidate_id in set(u.get("blocked_users", []))


# ──────────────────────────────────────────────────────────────
# CONNECT / DISCONNECT
# ──────────────────────────────────────────────────────────────

async def disconnect(context, u1: int, u2: int, requeue: bool = False, ender_id: int = None):
    for uid in (u1, u2):
        u = users.get(uid)
        if not u:
            continue
        partner = u.get("partner")

        if partner and not ALLOW_INSTANT_REMATCH and MAX_RECENT_PARTNERS > 0:
            recent = u.setdefault("recent_partners", [])
            if partner not in recent:
                recent.append(partner)
                if len(recent) > MAX_RECENT_PARTNERS:
                    recent.pop(0)

        u["partner"] = None
        u["state"] = "IDLE"
        u["pending_media"] = {}

    for uid in (u1, u2):
        if ender_id is not None and uid == ender_id:
            intro = "You ended the chat."
        elif ender_id is not None:
            intro = "Your partner ended the chat."
        else:
            intro = "The conversation has been closed."

        blocks = [
            {"type": "text", "content": intro},
            {"type": "divider"},
            {"type": "text", "content": "Ready to meet someone new?"},
            {"type": "divider"},
            {"type": "quote", "content": "Tap Find Partner or send /next to start again."},
        ]
        await safe_send(
            context, uid,
            box_card("Chat Ended", blocks, emoji="🛑"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )

    if requeue:
        u = users.get(u1)
        if u and not u.get("is_banned"):
            u["state"] = "SEARCHING"
            async with queue_lock:
                if u1 not in queue_set:
                    queue.append(u1)
                    queue_set.add(u1)
            name = u.get("name") or "there"
            body = box_card(
                "Searching",
                [
                    {"type": "text", "content": f"Hey {name}, looking for someone new..."},
                    {"type": "divider"},
                    {"type": "text", "content": "⏳ Sit tight, matching you with a fresh partner."},
                ],
                emoji="🔍",
            )
            await safe_send(context, u1, body, parse_mode="HTML")


async def connect_users(context, uid1: int, uid2: int):
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return

    u1["partner"] = uid2
    u1["state"] = "CHAT"
    u1["pending_media"] = {}
    u1["total_matches"] = u1.get("total_matches", 0) + 1
    u1["total_chats"] = u1.get("total_chats", 0) + 1

    u2["partner"] = uid1
    u2["state"] = "CHAT"
    u2["pending_media"] = {}
    u2["total_matches"] = u2.get("total_matches", 0) + 1
    u2["total_chats"] = u2.get("total_chats", 0) + 1

    connected_blocks = [
        {"type": "kv", "items": [
            (f"🔒 {to_bold('Privacy')}", to_bold("Encrypted")),
            (f"🎭 {to_bold('Identity')}", to_bold("Anonymous")),
            (f"🟢 {to_bold('Status')}", to_bold("Active")),
        ]},
        {"type": "divider"},
        {"type": "quote", "content": "Say Hi or ask a fun question to begin!"},
    ]

    footer_lines = [
        f"🔄 /next — {to_bold('New Partner')}",
        f"🛑 /end  — {to_bold('End Chat')}",
    ]

    connected_card = box_card("Woohoo! You're Connected", connected_blocks, emoji="✨")
    connected_card += "\n\n" + "\n".join(footer_lines)

    actions = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Next", callback_data="CHAT_NEXT"),
         InlineKeyboardButton("🛑 End", callback_data="CHAT_END")],
        [InlineKeyboardButton("🚨 Report", callback_data="CHAT_REPORT"),
         InlineKeyboardButton("🚫 Block", callback_data="CHAT_BLOCK")],
    ])

    await safe_send(context, uid1, connected_card, reply_markup=actions, parse_mode="HTML")
    await context.bot.send_message(
        chat_id=uid1, text="Chat controls:",
        reply_markup=get_chat_keyboard(u1),
    )

    await safe_send(context, uid2, connected_card, reply_markup=actions, parse_mode="HTML")
    await context.bot.send_message(
        chat_id=uid2, text="Chat controls:",
        reply_markup=get_chat_keyboard(u2),
    )


# ──────────────────────────────────────────────────────────────
# TRY MATCH
# ──────────────────────────────────────────────────────────────

async def try_match(context, uid: int):
    u = await get_user(uid)
    if not u or u.get("is_banned"):
        return

    name = u.get("name") or "there"

    if not u.get("gender"):
        return await safe_send(
            context, uid,
            box_simple("Setup Required", f"⚠️ Hey {name}, please run /start first.", emoji="⚠️"),
            parse_mode="HTML",
        )

    now_ts = utcnow().timestamp()
    last = last_next_time.get(uid, 0)
    if NEXT_COOLDOWN_SECONDS > 0 and now_ts - last < NEXT_COOLDOWN_SECONDS:
        remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last))
        return await safe_send(
            context, uid,
            box_simple("Please Wait", f"⏳ Hey {name}, wait {remaining}s before searching again.", emoji="⏳"),
            parse_mode="HTML",
        )
    last_next_time[uid] = now_ts

    if u.get("partner"):
        return await disconnect(context, uid, u["partner"], requeue=True, ender_id=uid)

    async with queue_lock:
        already_searching = (u.get("state") == "SEARCHING" and uid in queue_set)
    if already_searching:
        return await safe_send(
            context, uid,
            box_simple("Already Searching", f"🔍 Hey {name}, you're already in the queue...", emoji="🔍"),
            parse_mode="HTML",
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"
        await save_user_to_db(uid, u)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")],
        ])
        body = box_card(
            "VIP Needed",
            [
                {"type": "text", "content": "⚠️ Gender filter is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content": "Preference reset to Any."},
            ],
            emoji="👑",
        )
        return await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")

    found = None

    async with queue_lock:
        for candidate_id in list(queue):
            if candidate_id == uid:
                continue
            c = users.get(candidate_id)
            if not c or c.get("is_banned"):
                continue
            if c.get("state") != "SEARCHING" or c.get("partner"):
                continue

            if _is_recent(u, candidate_id):
                continue
            if _is_blocked(u, candidate_id):
                continue
            if _is_blocked(c, uid):
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
            queue_set.discard(found)
            queue_set.discard(uid)

    if found is not None:
        await connect_users(context, uid, found)
        return

    async with queue_lock:
        if uid not in queue_set:
            queue.append(uid)
            queue_set.add(uid)
        u["state"] = "SEARCHING"
        waiting = len(queue)

    body = box_card(
        "Searching",
        [
            {"type": "text", "content": f"Hey {name}, looking for someone to chat with..."},
            {"type": "divider"},
            {"type": "kv", "items": [(f"👥 {to_bold('Waiting')}", str(waiting))]},
        ],
        emoji="🔍",
    )
    await safe_send(context, uid, body, parse_mode="HTML")


# ──────────────────────────────────────────────────────────────
# BACKGROUND MATCHER
# ──────────────────────────────────────────────────────────────

async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    async with queue_lock:
        if len(queue) < 2:
            return
        waiting = list(queue)

    matched = set()
    pairs = []

    async with queue_lock:
        for i, uid1 in enumerate(waiting):
            if uid1 in matched:
                continue
            u1 = users.get(uid1)
            if not u1 or u1.get("state") != "SEARCHING" or u1.get("partner") or u1.get("is_banned"):
                continue

            for uid2 in waiting[i + 1:]:
                if uid2 in matched:
                    continue
                u2 = users.get(uid2)
                if not u2 or u2.get("state") != "SEARCHING" or u2.get("partner") or u2.get("is_banned"):
                    continue

                if _is_recent(u1, uid2) or _is_recent(u2, uid1):
                    continue
                if _is_blocked(u1, uid2) or _is_blocked(u2, uid1):
                    continue

                cond1 = u1.get("pref_gender", "Any") == "Any" or u2.get("gender") == u1.get("pref_gender")
                cond2 = u2.get("pref_gender", "Any") == "Any" or u1.get("gender") == u2.get("pref_gender")

                if cond1 and cond2:
                    matched.add(uid1)
                    matched.add(uid2)
                    pairs.append((uid1, uid2))
                    break

        for a, b in pairs:
            try:
                queue.remove(a)
            except ValueError:
                pass
            try:
                queue.remove(b)
            except ValueError:
                pass
            queue_set.discard(a)
            queue_set.discard(b)

    for a, b in pairs:
        await connect_users(context, a, b)


# ──────────────────────────────────────────────────────────────
# REPORT / BLOCK / END
# ──────────────────────────────────────────────────────────────

async def report_internal(context, uid: int):
    u = await get_user(uid)
    if not u or not u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ You are not in an active chat.", emoji="⚠️"),
            parse_mode="HTML",
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
        emoji="🚨",
    )
    for admin_id in admin_cache:
        if admin_id:
            await safe_send(context, admin_id, body, parse_mode="HTML")

    await safe_send(
        context, uid,
        box_simple(
            "Report Sent",
            "✅ Report sent to the team.\n\nThank you for helping keep SparkTalks safe.",
            emoji="✅",
        ),
        parse_mode="HTML",
    )


async def block_internal(context, uid: int):
    u = await get_user(uid)
    if not u:
        return

    # 🔒 VIP-only check
    if BLOCK_REQUIRES_VIP and not u.get("is_vip"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
        ])
        body = box_card(
            "VIP Required",
            [
                {"type": "text", "content": "🔒 Block is a VIP-only feature."},
                {"type": "divider"},
                {"type": "text", "content": "Upgrade to VIP to block unwanted users instantly."},
                {"type": "divider"},
                {"type": "section", "emoji": "👑", "heading": "VIP Perks"},
                {"type": "line", "content": "• 🚫 Block unwanted users"},
                {"type": "line", "content": "• 🚻 Gender filter"},
                {"type": "line", "content": "• ⚡ Priority matching"},
            ],
            emoji="🔒",
        )
        return await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")

    if not u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ You are not in an active chat.", emoji="⚠️"),
            parse_mode="HTML",
        )

    partner_id = u["partner"]
    blocked = u.setdefault("blocked_users", [])
    if partner_id not in blocked:
        blocked.append(partner_id)
        if len(blocked) > MAX_BLOCKED_USERS:
            u["blocked_users"] = blocked[-MAX_BLOCKED_USERS:]
    await save_user_to_db(uid, u)
    await disconnect(context, uid, partner_id, requeue=True, ender_id=uid)


async def end_chat_internal(context, uid: int):
    u = await get_user(uid)
    name = u.get("name") if u else "there"
    if not u or (not u.get("partner") and u.get("state") != "SEARCHING"):
        return await safe_send(
            context, uid,
            box_simple("Notice", f"⚠️ Hey {name}, you are not in a chat or search.", emoji="⚠️"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    if u.get("state") == "SEARCHING":
        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
        u["state"] = "IDLE"
        return await safe_send(
            context, uid,
            box_simple("Search Cancelled", f"🛑 Hey {name}, search stopped.", emoji="🛑"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    await disconnect(context, uid, u["partner"], ender_id=uid)