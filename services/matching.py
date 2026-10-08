import random
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    NEXT_COOLDOWN_SECONDS, MAX_RECENT_PARTNERS, MAX_BLOCKED_USERS,
    ALLOW_INSTANT_REMATCH, BLOCK_REQUIRES_VIP, BOT_USERNAME,
    VIP_PRIORITY_QUEUE,
)
from state import users, queue, queue_set, queue_lock, last_next_time, admin_cache, analytics
import state
from database import get_user, save_user_to_db, is_owner_or_admin
from utils import box_card, box_simple, safe_send, utcnow, to_bold
from keyboards import get_main_keyboard, get_chat_keyboard

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# THEMES
# ══════════════════════════════════════════════════════════════
THEMES = {
    "party":  {"tl": "🎉", "tr": "🎉", "mid": ["✨", "🎊", "🎈"], "p": "👤", "a": "⚡", "s": "🛡️"},
    "cosmic": {"tl": "💫", "tr": "💫", "mid": ["🌌", "⭐", "🌟"], "p": "🧑‍🚀", "a": "🚀", "s": "🛰️"},
    "fire":   {"tl": "🔥", "tr": "🔥", "mid": ["⚡", "💥", "🌟"], "p": "🎭", "a": "⚡", "s": "🛡️"},
    "sakura": {"tl": "🌸", "tr": "🌸", "mid": ["💮", "🌷", "🌺"], "p": "🍃", "a": "⚡", "s": "🛡️"},
    "cute":   {"tl": "🦦", "tr": "🦦", "mid": ["💖", "🐾", "🎀"], "p": "🐾", "a": "⚡", "s": "🛡️"},
    "royal":  {"tl": "👑", "tr": "👑", "mid": ["💎", "✨", "🏆"], "p": "✨", "a": "⚡", "s": "🛡️"},
}

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
    "💡 🎙️ Voice rooms for VIP members only! → /buy",
]

VIP_USER_TIPS = [
    "💡 Thanks for supporting SparkTalks! 💎",
    "💡 Enjoy unlimited /next as a VIP! 🚀",
    "💡 Use /settings → filter by gender! 🎯",
    "💡 VIP status: protected & prioritized! 👑",
    "💡 Try /voice for a live voice room! 🎙️",
]


# ══════════════════════════════════════════════════════════════
# HELPERS — Estimated wait + Search cards
# ══════════════════════════════════════════════════════════════

def _estimate_wait(waiting: int) -> int:
    """Estimate wait time (in seconds) based on real match data.

    Formula: avg / sqrt(waiting)
    - Uses historical average once >= 5 matches tracked
    - Falls back to 15s default otherwise
    - Clamps between 5s and 120s
    """
    if analytics["match_wait_count"] >= 5:
        avg = analytics["match_wait_total"] / analytics["match_wait_count"]
    else:
        avg = 15.0

    if waiting <= 1:
        est = int(avg)
    else:
        est = int(avg / (waiting ** 0.5))

    return max(5, min(est, 120))


def _build_searching_card(name: str, waiting: int, me: dict,
                          est_wait: int = None) -> str:
    """Stylish Searching screen — compact premium format."""
    if est_wait is None:
        est_wait = _estimate_wait(waiting)

    return (
        "🔍  ✨  Searching...  ✨  🔍\n"
        "▎\n"
        f"▎ 👋 Hey <b>{name}</b>, finding your match...\n"
        "▎\n"
        "▎ ⏳ <b>Status</b>\n"
        "▎   ├ 🔍 In queue\n"
        f"▎   ├ 👥 Waiting : <b>{waiting}</b> users\n"
        f"▎   └ ⚡️ Est. time : <b>~{est_wait}s</b>\n"
        "▎\n"
        "▎ 💡 <b>Tip</b>\n"
        "▎   └ Send /cancel to stop searching"
    )


def _build_cancel_card(name: str, me: dict = None) -> str:
    """Stylish Search Cancelled screen — bold heading."""
    if me and me.get("is_vip"):
        tip = "💡 VIP unlocked: priority queue + gender filter 🎯"
    else:
        tip = "💡 Ready for a fresh match anytime!"

    return (
        "🛑  ✨  <b>ꜱᴇᴀʀᴄʜ ᴄᴀɴᴄᴇʟʟᴇᴅ</b>  ✨  🛑\n"
        "▎\n"
        f"▎ ✅ Hey <b>{name}</b>, search stopped.\n"
        "▎\n"
        "▎ 🎯  <b>What's next</b>\n"
        "▎   ├ 🎲 /next — Search again\n"
        "▎   ├ ⚙️ /settings — Preferences\n"
        "▎   └ 🛍️ /buy — Unlock VIP\n"
        "▎\n"
        f"<i>{tip}</i>"
    )


def _build_already_searching_card(name: str, waiting: int,
                                   est_wait: int = None) -> str:
    """Stylish 'Already Searching' screen — same style as searching."""
    if est_wait is None:
        est_wait = _estimate_wait(waiting)

    return (
        "🔍  ✨  <b>Already Searching</b>  ✨  🔍\n"
        "▎\n"
        f"▎ 👋 Hey <b>{name}</b>, you're already in queue...\n"
        "▎\n"
        "▎ ⏳ <b>Status</b>\n"
        "▎   ├ 🔍 In queue\n"
        f"▎   ├ 👥 Waiting : <b>{waiting}</b> users\n"
        f"▎   └ ⚡️ Est. time : <b>~{est_wait}s</b>\n"
        "▎\n"
        "▎ 💡 <b>Tip</b>\n"
        "▎   └ Send /cancel to stop searching"
    )


# ══════════════════════════════════════════════════════════════
# THEME / TIP / CONNECT CARD HELPERS
# ══════════════════════════════════════════════════════════════

def _pick_theme(me: dict) -> dict:
    chosen = me.get("theme")
    if chosen and chosen in THEMES:
        return THEMES[chosen]
    if me.get("is_vip"):
        return THEMES["royal"]
    keys = list(THEMES.keys())[:5]  # non-royal
    return THEMES[keys[me.get("total_matches", 0) % len(keys)]]


def _get_tip(me: dict) -> str:
    mc = me.get("total_matches", 0)
    if me.get("is_vip"):
        return VIP_USER_TIPS[mc % len(VIP_USER_TIPS)]
    return VIP_TIPS_POOL[mc % len(VIP_TIPS_POOL)]


def _build_connect_card(me: dict, partner: dict, common: list) -> str:
    theme = _pick_theme(me)
    mid = random.choice(theme["mid"])

    if me.get("is_vip"):
        g = partner.get("gender") or "Unknown"
        gender_line = {"Female": "👩 Female", "Male": "👨 Male"}.get(g, f"👤 {g}")
        block_line = "🚫 /block  — Block & skip"
        voice_line = "🎙️ /voice  — Anonymous voice call"
    else:
        vip_link = f"https://t.me/{BOT_USERNAME}?start=vip"
        gender_line = f'🔒 Hidden  <a href="{vip_link}">👑 VIP Only</a>'
        block_line = f'🚫 /block  — <a href="{vip_link}">👑 VIP Only</a>'
        voice_line = f'🎙️ /voice  — <a href="{vip_link}">👑 VIP Only</a>'

    common_line = ", ".join(common) if common else "—"

    title = f"{theme['tl']}  {mid}  <b>Chat Connected!</b>  {mid}  {theme['tr']}"
    tip_line = _get_tip(me)

    lines = [
        title,
        "▎",
        f"▎ <b>{theme['p']}  Partner Info</b>",
        f"▎   ├ 🚻 Gender : {gender_line}",
        f"▎   └ 🎯 Common : {common_line}",
        "▎",
        "▎ 💬  Say hello to start chatting...",
        "▎",
        f"▎ <b>{theme['a']}  Actions</b>",
        "▎   ├ 🔄 /next   — Find new partner",
        "▎   ├ 🛑 /end    — Stop the chat",
        f"▎   └ {voice_line}",
        "▎",
        f"▎ <b>{theme['s']}  Safety</b>",
        "▎   ├ 🚨 /report — Report partner",
        f"▎   └ {block_line}",
        "▎",
        "",
        f"<i>{tip_line}</i>",
    ]
    return "\n".join(lines)


def _is_recent(u: dict, candidate_id: int) -> bool:
    if ALLOW_INSTANT_REMATCH or MAX_RECENT_PARTNERS <= 0:
        return False
    return candidate_id in set(u.get("recent_partners", []))


def _is_blocked(u: dict, candidate_id: int) -> bool:
    return candidate_id in set(u.get("blocked_users", []))


# ══════════════════════════════════════════════════════════════
# DISCONNECT
# ══════════════════════════════════════════════════════════════

async def disconnect(context, u1: int, u2: int, requeue: bool = False,
                     ender_id: int = None, notify_ender: bool = True):
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
            f"▎ 🔄 <b>Next Steps</b>\n"
            f"▎   ├ 🎲 /next — Find new partner\n"
            f"▎   ├ 👤 /profile — View your stats\n"
            f"▎   └ 🏠 /start — Back to dashboard\n"
            f"▎\n"
            f"▎ 💡 Ready to meet someone new? 👇"
        )
        await safe_send(context, uid, body, parse_mode="HTML",
                        reply_markup=get_main_keyboard())

        # Feedback buttons
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👍 Good match", callback_data="FB_GOOD", style="success"),
            InlineKeyboardButton("👎 Bad match", callback_data="FB_BAD", style="danger"),
        ]])
        await safe_send(
            context, uid,
            "📝 <i>Was this a good match? Your feedback helps us improve!</i>",
            reply_markup=kb, parse_mode="HTML",
        )

    if requeue:
        u = users.get(u1)
        if u and not u.get("is_banned"):
            u["state"] = "SEARCHING"
            async with queue_lock:
                if u1 not in queue_set:
                    if VIP_PRIORITY_QUEUE and u.get("is_vip"):
                        queue.appendleft(u1)
                    else:
                        queue.append(u1)
                    queue_set.add(u1)
                waiting = len(queue)
            name = u.get("name") or "there"
            body = _build_searching_card(name, waiting, u)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ ꜱᴇᴀʀᴄʜ", callback_data="CANCEL_SEARCH",
                                     style="danger"),
            ]])
            await safe_send(context, u1, body, reply_markup=kb, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# CONNECT USERS
# ══════════════════════════════════════════════════════════════

async def connect_users(context, uid1: int, uid2: int):
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return

    for uid, partner_id in ((uid1, uid2), (uid2, uid1)):
        u = users.get(uid)
        u["partner"] = partner_id
        u["state"] = "CHAT"
        u["pending_media"] = {}
        u["total_matches"] = u.get("total_matches", 0) + 1

        # ⭐ Track wait time for estimated wait calculation
        started = last_next_time.get(uid)
        if started:
            waited = utcnow().timestamp() - started
            if 0 < waited < 600:  # only count reasonable waits (< 10 min)
                analytics["match_wait_total"] += waited
                analytics["match_wait_count"] += 1

    analytics["matches_today"] += 1

    common = list(set(u1.get("interests", [])) & set(u2.get("interests", [])))

    await safe_send(
        context, uid1,
        _build_connect_card(u1, u2, common),
        reply_markup=get_chat_keyboard(u1),
        parse_mode="HTML", disable_web_page_preview=True,
    )
    await safe_send(
        context, uid2,
        _build_connect_card(u2, u1, common),
        reply_markup=get_chat_keyboard(u2),
        parse_mode="HTML", disable_web_page_preview=True,
    )


# ══════════════════════════════════════════════════════════════
# TRY MATCH
# ══════════════════════════════════════════════════════════════

async def try_match(context, uid: int):
    u = await get_user(uid)
    if not u or u.get("is_banned"):
        return

    name = u.get("name") or "there"

    # MAINTENANCE
    if state.maintenance_mode and not await is_owner_or_admin(uid):
        return await safe_send(
            context, uid,
            "🚧  ✨  <b>Under Maintenance</b>  ✨  🚧\n"
            "▎\n"
            "▎ 🛠️ We're upgrading SparkTalks.\n"
            "▎ ⏰ Please try again in a few minutes.",
            parse_mode="HTML",
        )

    if not u.get("gender"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE", style="primary"),
            InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE", style="primary"),
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

    if not u.get("is_vip") and NEXT_COOLDOWN_SECONDS > 0:
        now_ts = utcnow().timestamp()
        last = last_next_time.get(uid, 0)
        if now_ts - last < NEXT_COOLDOWN_SECONDS:
            remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last))
            return await safe_send(
                context, uid,
                box_simple("Please Wait",
                           f"⏳ Hey {name}, wait {remaining}s before searching again.",
                           emoji="⏳"),
                parse_mode="HTML",
            )
    last_next_time[uid] = utcnow().timestamp()

    if u.get("partner"):
        return await disconnect(context, uid, u["partner"],
                                requeue=True, ender_id=uid, notify_ender=False)

    async with queue_lock:
        already_searching = (u.get("state") == "SEARCHING" and uid in queue_set)
    if already_searching:
        waiting = len(queue)
        return await safe_send(
            context, uid,
            _build_already_searching_card(name, waiting),
            parse_mode="HTML",
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"
        await save_user_to_db(uid, u)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE", style="primary")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")],
        ])
        body = box_card("VIP Needed", [
            {"type": "text", "content": "⚠️ Gender filter is a VIP feature."},
            {"type": "divider"},
            {"type": "text", "content": "Preference reset to Any."},
        ], emoji="👑")
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
            if _is_blocked(u, candidate_id) or _is_blocked(c, uid):
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
            if VIP_PRIORITY_QUEUE and u.get("is_vip"):
                queue.appendleft(uid)
            else:
                queue.append(uid)
            queue_set.add(uid)
        u["state"] = "SEARCHING"
        waiting = len(queue)

    body = _build_searching_card(name, waiting, u)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ ꜱᴇᴀʀᴄʜ", callback_data="CANCEL_SEARCH",
                             style="danger"),
    ]])
    await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# BACKGROUND MATCHER
# ══════════════════════════════════════════════════════════════

async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    if state.maintenance_mode:
        return

    async with queue_lock:
        if len(queue) < 2:
            return
        waiting = list(queue)

    matched = set()
    pairs = []

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

    if pairs:
        async with queue_lock:
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


# ══════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════

async def report_internal(context, uid: int):
    u = await get_user(uid)
    if not u or not u.get("partner"):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ You are not in an active chat.", emoji="⚠️"),
            parse_mode="HTML",
        )
    partner_id = u["partner"]

    # Increment report_count
    partner = users.get(partner_id)
    if partner:
        partner["report_count"] = partner.get("report_count", 0) + 1
        await save_user_to_db(partner_id, partner)
    from database import users_collection
    if users_collection is not None:
        try:
            await users_collection.update_one(
                {"user_id": partner_id},
                {"$inc": {"report_count": 1}},
            )
        except Exception:
            pass
    analytics["reports_today"] += 1

    admin_body = box_card("Report Received", [
        {"type": "kv", "items": [
            (f"👤 Reporter", f"<code>{uid}</code>"),
            (f"🎯 Reported", f"<code>{partner_id}</code>"),
        ]},
        {"type": "divider"},
        {"type": "text", "content": f"⏰ {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"},
    ], emoji="🚨")
    for admin_id in admin_cache:
        if admin_id:
            await safe_send(context, admin_id, admin_body, parse_mode="HTML")

    user_body = (
        f"🚨  ✨  <b>Report Sent</b>  ✨  🚨\n"
        f"▎\n"
        f"▎ ✅ Thank you for reporting!\n"
        f"▎\n"
        f"▎ 🔒 Partner NOT notified\n"
        f"▎ 💬 Chat continues\n"
        f"▎ 👮 Admins will review\n"
        f"▎\n"
        f"▎ 💡 Use /block to stop this user (VIP)"
    )
    await safe_send(context, uid, user_body, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# BLOCK
# ══════════════════════════════════════════════════════════════

async def block_internal(context, uid: int):
    u = await get_user(uid)
    if not u:
        return
    if BLOCK_REQUIRES_VIP and not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE", style="primary"),
        ]])
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

    for pid in (uid, partner_id):
        pu = users.get(pid)
        if pu:
            pu["partner"] = None
            pu["state"] = "IDLE"
            pu["pending_media"] = {}

    total = len(u.get("blocked_users", []))
    block_body = (
        f"🚫  ✨  <b>User Blocked</b>  ✨  🚫\n"
        f"▎\n"
        f"▎ 🛡️ You blocked this user.\n"
        f"▎\n"
        f"▎ 🚫 Blocked : <b>1 user</b>\n"
        f"▎ 📊 Total blocked : <b>{total}</b>\n"
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
        f"▎ 🔄 /next — Find new partner\n"
        f"▎ 🏠 /start — Back to dashboard"
    )
    await safe_send(context, partner_id, partner_body,
                    reply_markup=get_main_keyboard(), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# CANCEL SEARCH
# ══════════════════════════════════════════════════════════════

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
    body = _build_cancel_card(name, u)
    await safe_send(context, uid, body, parse_mode="HTML",
                    reply_markup=get_main_keyboard())


# ══════════════════════════════════════════════════════════════
# END CHAT
# ══════════════════════════════════════════════════════════════

async def end_chat_internal(context, uid: int):
    u = await get_user(uid)
    name = u.get("name") if u else "there"
    if not u or (not u.get("partner") and u.get("state") != "SEARCHING"):
        return await safe_send(
            context, uid,
            box_simple("Notice",
                       f"⚠️ Hey {name}, you are not in a chat or search.",
                       emoji="⚠️"),
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
        body = _build_cancel_card(name, u)
        return await safe_send(context, uid, body, parse_mode="HTML",
                               reply_markup=get_main_keyboard())
    await disconnect(context, uid, u["partner"], ender_id=uid)