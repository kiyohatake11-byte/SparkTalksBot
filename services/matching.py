import random
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    NEXT_COOLDOWN_SECONDS,
    MAX_RECENT_PARTNERS,
    MAX_BLOCKED_USERS,
    ALLOW_INSTANT_REMATCH,
    BLOCK_REQUIRES_VIP,
    BOT_USERNAME,
)
from state import users, queue, queue_set, queue_lock, last_next_time, admin_cache
from database import get_user, save_user_to_db
from utils import box_card, box_simple, safe_send, utcnow, to_bold
from keyboards import get_main_keyboard, get_chat_keyboard

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# CONNECT CARD — THEMED + VIP UPSELL TIPS
# ══════════════════════════════════════════════════════════════

THEMES = [
    {
        "name": "party",
        "title_left": "🎉", "title_right": "🎉",
        "title_mid": ["✨", "🎊", "🎈"],
        "partner": "👤", "actions": "⚡", "safety": "🛡️",
    },
    {
        "name": "cosmic",
        "title_left": "💫", "title_right": "💫",
        "title_mid": ["🌌", "⭐", "🌟"],
        "partner": "🧑‍🚀", "actions": "🚀", "safety": "🛰️",
    },
    {
        "name": "fire",
        "title_left": "🔥", "title_right": "🔥",
        "title_mid": ["⚡", "💥", "🌟"],
        "partner": "🎭", "actions": "⚡", "safety": "🛡️",
    },
    {
        "name": "sakura",
        "title_left": "🌸", "title_right": "🌸",
        "title_mid": ["💮", "🌷", "🌺"],
        "partner": "🍃", "actions": "⚡", "safety": "🛡️",
    },
    {
        "name": "cute",
        "title_left": "🦦", "title_right": "🦦",
        "title_mid": ["💖", "🐾", "🎀"],
        "partner": "🐾", "actions": "⚡", "safety": "🛡️",
    },
    {
        "name": "royal",
        "title_left": "👑", "title_right": "👑",
        "title_mid": ["💎", "✨", "🏆"],
        "partner": "✨", "actions": "⚡", "safety": "🛡️",
    },
]


VIP_TIPS_POOL = [
    "💡 /buy → unlock gender filter & block!",
    "💡 VIP = unlimited /next with no cooldown! ⚡",
    "💡 Tired of 2s wait? VIP = instant next! 🚀",
    "💡 Stand out with 👑 VIP badge in /profile!",
    "💡 VIP users see partner's gender instantly! 👀",
    "💡 Skip wrong matches — VIP gender filter! 🎯",
    "💡 VIP = priority matching in queue! ⚡",
    "💡 Starting at just ₹99 → 14 days VIP! → /buy",
    "💡 Block unwanted users with VIP → /buy 👑",
    "💡 VIP unlocks: filter + block + no-cooldown! 🚀",
]

VIP_USER_TIPS = [
    "💡 Thanks for supporting SparkTalks! 💎",
    "💡 Enjoy unlimited /next as a VIP! 🚀",
    "💡 Use /settings → filter by gender! 🎯",
    "💡 VIP status: protected & prioritized! 👑",
]


def _pick_theme(me: dict) -> dict:
    if me.get("is_vip"):
        return THEMES[5]
    return THEMES[me.get("total_matches", 0) % 5]


def _get_tip(me: dict) -> str:
    mc = me.get("total_matches", 0)
    if me.get("is_vip"):
        return VIP_USER_TIPS[mc % len(VIP_USER_TIPS)]
    return VIP_TIPS_POOL[mc % len(VIP_TIPS_POOL)]


def _build_connect_card(me: dict, partner: dict, common: list) -> str:
    theme = _pick_theme(me)
    mid_emoji = random.choice(theme["title_mid"])

    if me.get("is_vip"):
        g = partner.get("gender") or "Unknown"
        gender_line = {"Female": "👩 Female", "Male": "👨 Male"}.get(g, f"👤 {g}")
    else:
        vip_link = f"https://t.me/{BOT_USERNAME}?start=vip"
        gender_line = f'🔒 Hidden  <a href="{vip_link}">👑 VIP Only</a>'

    if me.get("is_vip"):
        block_line = "🚫 /block  — Block & skip"
    else:
        vip_link = f"https://t.me/{BOT_USERNAME}?start=vip"
        block_line = f'🚫 /block  — <a href="{vip_link}">👑 VIP Only</a>'

    common_line = ", ".join(common) if common else "—"

    title = (
        f"{theme['title_left']}  {mid_emoji}  "
        f"<b>Chat Connected!</b>  {mid_emoji}  {theme['title_right']}"
    )
    tip_line = _get_tip(me)

    lines = [
        title,
        "▎",
        f"▎ <b>{theme['partner']}  Partner Info</b>",
        f"▎   ├ 🚻 Gender : {gender_line}",
        f"▎   └ 🎯 Common : {common_line}",
        "▎",
        "▎ 💬  Say hello to start chatting...",
        "▎",
        f"▎ <b>{theme['actions']}  Actions</b>",
        "▎   ├ 🔄 /next   — Find new partner",
        "▎   └ 🛑 /end    — Stop the chat",
        "▎",
        f"▎ <b>{theme['safety']}  Safety</b>",
        "▎   ├ 🚨 /report — Report partner",
        f"▎   └ {block_line}",
        "▎",
        "",
        f"<i>{tip_line}</i>",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# HELPERS
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

async def disconnect(context, u1: int, u2: int, requeue: bool = False,
                     ender_id: int = None, notify_ender: bool = True):
    """
    Disconnect two users.
    
    Args:
        u1, u2: User IDs
        requeue: If True, re-add u1 to queue
        ender_id: Who triggered the disconnect
        notify_ender: If False, don't send "Chat Ended" to the ender
    """
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

    # ─── Send "Chat Ended" message ───
    for uid in (u1, u2):
        # 🆕 Skip ender if notify_ender=False
        if ender_id is not None and uid == ender_id and not notify_ender:
            continue

        if ender_id is not None and uid == ender_id:
            reason = "You ended the chat."
        elif ender_id is not None:
            reason = "Your partner ended the chat."
        else:
            reason = "The conversation was closed."

        body = (
            f"🛑  ✨  <b>Chat Ended</b>  ✨  🛑\n"
            f"▎\n"
            f"▎ 💔 <b>{reason}</b>\n"
            f"▎\n"
            f"▎ 📊 <b>Session Summary</b>\n"
            f"▎   ├ ⏱ Duration : <b>—</b>\n"
            f"▎   └ 🎯 Reason : <b>Manual end</b>\n"
            f"▎\n"
            f"▎ 🔄 <b>Next Steps</b>\n"
            f"▎   ├ 🎲 /next — Find new partner\n"
            f"▎   ├ 👤 /profile — View your stats\n"
            f"▎   └ 🏠 /start — Back to dashboard\n"
            f"▎\n"
            f"▎ 💡 Ready to meet someone new? 👇"
        )
        await safe_send(
            context, uid, body,
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )

    # ─── Requeue if requested ───
    if requeue:
        u = users.get(u1)
        if u and not u.get("is_banned"):
            u["state"] = "SEARCHING"
            async with queue_lock:
                if u1 not in queue_set:
                    queue.append(u1)
                    queue_set.add(u1)
            name = u.get("name") or "there"
            waiting = len(queue)
            body = (
                f"🔍  ✨  <b>Searching...</b>  ✨  🔍\n"
                f"▎\n"
                f"▎ 👋 Hey <b>{name}</b>, finding your match...\n"
                f"▎\n"
                f"▎ ⏳ <b>Status</b>\n"
                f"▎   ├ 🔍 In queue\n"
                f"▎   ├ 👥 Waiting : <b>{waiting}</b> users\n"
                f"▎   └ ⚡ Est. time : <b>~15s</b>\n"
                f"▎\n"
                f"▎ 💡 <b>Tip</b>\n"
                f"▎   └ Send /cancel to stop searching\n"
                f"▎"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel Search", callback_data="CANCEL_SEARCH"),
            ]])
            await safe_send(context, u1, body, reply_markup=kb, parse_mode="HTML")


async def connect_users(context, uid1: int, uid2: int):
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return

    # Set partner + state + stats
    for uid, partner_id in ((uid1, uid2), (uid2, uid1)):
        u = users.get(uid)
        u["partner"] = partner_id
        u["state"] = "CHAT"
        u["pending_media"] = {}
        u["total_matches"] = u.get("total_matches", 0) + 1
        u["total_chats"] = u.get("total_chats", 0) + 1

    common = list(set(u1.get("interests", [])) & set(u2.get("interests", [])))

    await safe_send(
        context, uid1,
        _build_connect_card(u1, u2, common),
        reply_markup=get_chat_keyboard(u1),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await safe_send(
        context, uid2,
        _build_connect_card(u2, u1, common),
        reply_markup=get_chat_keyboard(u2),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ──────────────────────────────────────────────────────────────
# TRY MATCH
# ──────────────────────────────────────────────────────────────

async def try_match(context, uid: int):
    u = await get_user(uid)
    if not u or u.get("is_banned"):
        return

    name = u.get("name") or "there"

    # Gender not set → show gender selection
    if not u.get("gender"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
            InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
        ]])
        return await safe_send(
            context, uid,
            box_card("Setup Required", [
                {"type": "text", "content": f"⚠️ Hey {name}, please set your gender first."},
                {"type": "divider"},
                {"type": "text", "content": "Select below 👇"},
            ], emoji="⚠️"),
            reply_markup=kb, parse_mode="HTML",
        )

    # Cooldown — skip for VIP
    if not u.get("is_vip") and NEXT_COOLDOWN_SECONDS > 0:
        now_ts = utcnow().timestamp()
        last = last_next_time.get(uid, 0)
        if now_ts - last < NEXT_COOLDOWN_SECONDS:
            remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last))
            return await safe_send(
                context, uid,
                box_simple("Please Wait", f"⏳ Hey {name}, wait {remaining}s before searching again.", emoji="⏳"),
                parse_mode="HTML",
            )
    last_next_time[uid] = utcnow().timestamp()

    # 🆕 Already in chat → disconnect + requeue (skip "Chat Ended" for ender)
    if u.get("partner"):
        return await disconnect(
            context, uid, u["partner"],
            requeue=True,
            ender_id=uid,
            notify_ender=False,
        )

    async with queue_lock:
        already_searching = (u.get("state") == "SEARCHING" and uid in queue_set)
    if already_searching:
        return await safe_send(
            context, uid,
            box_simple("Already Searching", f"🔍 Hey {name}, you are already in the queue...", emoji="🔍"),
            parse_mode="HTML",
        )

    # Non-VIP with gender filter
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

    body = (
        f"🔍  ✨  <b>Searching...</b>  ✨  🔍\n"
        f"▎\n"
        f"▎ 👋 Hey <b>{name}</b>, finding your match...\n"
        f"▎\n"
        f"▎ ⏳ <b>Status</b>\n"
        f"▎   ├ 🔍 In queue\n"
        f"▎   ├ 👥 Waiting : <b>{waiting}</b> users\n"
        f"▎   └ ⚡ Est. time : <b>~15s</b>\n"
        f"▎\n"
        f"▎ 💡 <b>Tip</b>\n"
        f"▎   └ Send /cancel to stop searching\n"
        f"▎"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel Search", callback_data="CANCEL_SEARCH"),
    ]])
    await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")


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
# REPORT / BLOCK / END / CANCEL
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

    admin_body = box_card(
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
            await safe_send(context, admin_id, admin_body, parse_mode="HTML")

    user_body = (
        f"🚨  ✨  <b>Report Sent</b>  ✨  🚨\n"
        f"▎\n"
        f"▎ ✅ Thank you for reporting!\n"
        f"▎\n"
        f"▎ 📋 <b>Report Details</b>\n"
        f"▎   ├ 🎯 Target : <code>hidden</code>\n"
        f"▎   ├ ⏰ Time : <b>just now</b>\n"
        f"▎   └ 🛡️ Status : <b>Received</b>\n"
        f"▎\n"
        f"▎ ℹ️ <b>What Happens Next</b>\n"
        f"▎   ├ 🔒 Partner NOT notified\n"
        f"▎   ├ 💬 Chat continues\n"
        f"▎   └ 👮 Admins will review\n"
        f"▎\n"
        f"▎ 💡 Use /block to stop this user (VIP)"
    )
    await safe_send(context, uid, user_body, parse_mode="HTML")


async def block_internal(context, uid: int):
    u = await get_user(uid)
    if not u:
        return

    if BLOCK_REQUIRES_VIP and not u.get("is_vip"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
        ])
        body = (
            f"🔒  ✨  <b>VIP Required</b>  ✨  🔒\n"
            f"▎\n"
            f"▎ ⚠️ <b>Block is a VIP feature.</b>\n"
            f"▎\n"
            f"▎ 👑 <b>Unlock with VIP</b>\n"
            f"▎   ├ 🚫 Block unwanted users\n"
            f"▎   ├ 🚻 Gender filter\n"
            f"▎   ├ ⚡ Priority matching\n"
            f"▎   └ 🔄 Unlimited /next\n"
            f"▎\n"
            f"▎ 💰 <b>Pricing</b>\n"
            f"▎   ├ 🚀 14 days : <b>₹99</b>\n"
            f"▎   ├ 🥇 1 month : <b>₹179</b>\n"
            f"▎   └ 💎 3 months : <b>₹449</b>\n"
            f"▎\n"
            f"▎ 💡 Tap below to view plans"
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

    # Clear state for both
    for pid in (uid, partner_id):
        pu = users.get(pid)
        if pu:
            pu["partner"] = None
            pu["state"] = "IDLE"
            pu["pending_media"] = {}

    total_blocked = len(u.get("blocked_users", []))
    block_body = (
        f"🚫  ✨  <b>User Blocked</b>  ✨  🚫\n"
        f"▎\n"
        f"▎ 🛡️ <b>You blocked this user.</b>\n"
        f"▎\n"
        f"▎ 📋 <b>Block Summary</b>\n"
        f"▎   ├ 🚫 Blocked : <b>1 user</b>\n"
        f"▎   ├ 🔄 Future : <b>No re-match</b>\n"
        f"▎   └ 📊 Total blocked : <b>{total_blocked}</b>\n"
        f"▎\n"
        f"▎ ✅ <b>What Happens</b>\n"
        f"▎   ├ 🔒 You won't see them again\n"
        f"▎   ├ 💬 Chat ended\n"
        f"▎   └ 🎯 Match quality improved\n"
        f"▎\n"
        f"▎ 💡 Ready for a better match? 👇"
    )
    await safe_send(context, uid, block_body,
                    reply_markup=get_main_keyboard(), parse_mode="HTML")

    partner_body = (
        f"🛑  ✨  <b>Chat Ended</b>  ✨  🛑\n"
        f"▎\n"
        f"▎ 💔 Your partner ended the chat.\n"
        f"▎\n"
        f"▎ 🔄 <b>Next Steps</b>\n"
        f"▎   ├ 🎲 /next — Find new partner\n"
        f"▎   └ 🏠 /start — Back to dashboard\n"
        f"▎\n"
        f"▎ 💡 Ready to meet someone new? 👇"
    )
    await safe_send(context, partner_id, partner_body,
                    reply_markup=get_main_keyboard(), parse_mode="HTML")


async def cancel_search(context, uid: int):
    u = await get_user(uid)
    if not u:
        return
    name = u.get("name") or "there"
    async with queue_lock:
        try:
            queue.remove(uid)
        except ValueError:
            pass
        queue_set.discard(uid)
    if u.get("state") == "SEARCHING":
        u["state"] = "IDLE"
    body = (
        f"🛑  ✨  <b>Search Cancelled</b>  ✨  🛑\n"
        f"▎\n"
        f"▎ ✅ Hey <b>{name}</b>, search stopped.\n"
        f"▎\n"
        f"▎ 🔄 <b>Try Again</b>\n"
        f"▎   ├ 🎲 /next — Start searching\n"
        f"▎   └ 🏠 /start — Dashboard\n"
        f"▎\n"
        f"▎ 💡 Search anytime with /next"
    )
    await safe_send(context, uid, body, parse_mode="HTML",
                    reply_markup=get_main_keyboard())


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
        body = (
            f"🛑  ✨  <b>Search Cancelled</b>  ✨  🛑\n"
            f"▎\n"
            f"▎ ✅ Hey <b>{name}</b>, search stopped.\n"
            f"▎\n"
            f"▎ 🎲 /next — Search again anytime\n"
            f"▎\n"
            f"▎ 💡 Ready when you are!"
        )
        return await safe_send(context, uid, body, parse_mode="HTML",
                               reply_markup=get_main_keyboard())
    await disconnect(context, uid, u["partner"], ender_id=uid)