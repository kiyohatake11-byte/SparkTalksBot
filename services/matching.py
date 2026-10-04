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

# ─── 6 Dynamic Themes ───
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


# ─── VIP Upsell Tips (for free users) ───
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

# ─── Tips for VIP users ───
VIP_USER_TIPS = [
    "💡 Thanks for supporting SparkTalks! 💎",
    "💡 Enjoy unlimited /next as a VIP! 🚀",
    "💡 Use /settings → filter by gender! 🎯",
    "💡 VIP status: protected & prioritized! 👑",
]


def _pick_theme(me: dict) -> dict:
    """VIP → Royal theme. Free → rotate 5 themes."""
    if me.get("is_vip"):
        return THEMES[5]
    return THEMES[me.get("total_matches", 0) % 5]


def _get_tip(me: dict) -> str:
    """VIP tips for free users, thanks tips for VIP users."""
    mc = me.get("total_matches", 0)
    if me.get("is_vip"):
        return VIP_USER_TIPS[mc % len(VIP_USER_TIPS)]
    return VIP_TIPS_POOL[mc % len(VIP_TIPS_POOL)]


def _build_connect_card(me: dict, partner: dict, common: list) -> str:
    """Attractive Sidebar Card with theme + VIP upsell tip."""

    # ── Pick theme ──
    theme = _pick_theme(me)
    mid_emoji = random.choice(theme["title_mid"])

    # ── Gender line (VIP gated) ──
    if me.get("is_vip"):
        g = partner.get("gender") or "Unknown"
        gender_line = {"Female": "👩 Female", "Male": "👨 Male"}.get(g, f"👤 {g}")
    else:
        vip_link = f"https://t.me/{BOT_USERNAME}?start=vip"
        gender_line = f'🔒 Hidden  <a href="{vip_link}">👑 VIP Only</a>'

    # ── Block line (VIP gated) ──
    if me.get("is_vip"):
        block_line = "🚫 /block  — Block & skip"
    else:
        vip_link = f"https://t.me/{BOT_USERNAME}?start=vip"
        block_line = f'🚫 /block  — <a href="{vip_link}">👑 VIP Only</a>'

    common_line = ", ".join(common) if common else "—"

    # ── Title ──
    title = (
        f"{theme['title_left']}  {mid_emoji}  "
        f"<b>Chat Connected!</b>  {mid_emoji}  {theme['title_right']}"
    )

    # ── Tip ──
    tip_line = _get_tip(me)

    # ── Build card ──
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

    # Common interests
    common = list(set(u1.get("interests", [])) & set(u2.get("interests", [])))

    # Send to both — each gets own themed card with own tip
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

    # Already in chat → disconnect + requeue
    if u.get("partner"):
        return await disconnect(context, uid, u["partner"], requeue=True, ender_id=uid)

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

    body = box_card(
        "Searching",
        [
            {"type": "text", "content": f"Hey {name}, looking for someone to chat with..."},
            {"type": "divider"},
            {"type": "kv", "items": [(f"👥 {to_bold('Waiting')}", str(waiting))]},
        ],
        emoji="🔍",
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
        box_card("Report Sent", [
            {"type": "text", "content": "✅ Report sent to the team."},
            {"type": "divider"},
            {"type": "text", "content": "Your partner has NOT been notified."},
            {"type": "text", "content": "Chat continues normally."},
            {"type": "divider"},
            {"type": "quote", "content": "Thank you for keeping SparkTalks safe."},
        ], emoji="✅"),
        parse_mode="HTML",
    )


async def block_internal(context, uid: int):
    u = await get_user(uid)
    if not u:
        return

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

    # requeue=False — user decides next step
    await disconnect(context, uid, partner_id, requeue=False, ender_id=uid)


async def cancel_search(context, uid: int):
    """Cancel current search & remove from queue."""
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
    await safe_send(
        context, uid,
        box_simple("Search Cancelled", f"🛑 Hey {name}, search stopped.", emoji="🛑"),
        parse_mode="HTML", reply_markup=get_main_keyboard(),
    )


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