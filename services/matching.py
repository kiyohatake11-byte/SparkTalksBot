"""Matching service — SIMPLE, FOCUSED, BUG-FREE.

⭐ FEATURES:
  1. Everyone matches everyone (default pref = "Any")
  2. Non-VIP users always use pref_gender="Any" (ignored)
  3. Only VIP users can use gender filter (Male/Female)
  4. Simple FIFO queue (no buckets, no complexity)
  5. Background matcher every 0.5s
  6. Auto-cleanup of stale users
  7. Race-free with single asyncio.Lock
"""
import asyncio
import random
import logging
from telegram import InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    NEXT_COOLDOWN_SECONDS, ALLOW_INSTANT_REMATCH,
    BOT_USERNAME, VIP_PRIORITY_QUEUE, MAX_RECENT_PARTNERS,
)
from state import (
    users, queue, queue_set, queue_lock, last_next_time,
    analytics,
)
import state
from database import get_user, save_user_to_db, is_owner_or_admin
from utils import box_card, box_simple, safe_send, utcnow, esc, ikb, log_event
from keyboards import get_main_keyboard, get_chat_keyboard
from services.community import should_prompt_join, prompt_join

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# THEMES & TIPS
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
    "💡 Stand out with 👑 VIP badge in /profile!",
    "💡 VIP users see partner's gender instantly! 👀",
    "💡 Skip wrong matches — VIP gender filter! 🎯",
    "💡 Starting at just ₹149 → 14 days VIP! → /buy",
    "💡 Block unwanted users with VIP → /buy 👑",
    "💡 🎙️ Voice rooms for VIP members only! → /buy",
]

VIP_USER_TIPS = [
    "💡 Thanks for supporting SparkTalks! 💎",
    "💡 Enjoy unlimited /next as a VIP! 🚀",
    "💡 Use /settings → filter by gender! 🎯",
    "💡 Try /voice for a live voice room! 🎙️",
    "💡 Reconnect & Save Partner — VIP-only perks! 💾",
]


# ══════════════════════════════════════════════════════════════
# ⭐ BACKWARD-COMPAT STUBS (for cleanup.py etc.)
# ══════════════════════════════════════════════════════════════

def _add_to_bucket(uid: int, u: dict) -> None:
    """No-op — buckets removed in v2. Kept for backward compat."""
    pass


def _remove_from_bucket_by_uid(uid: int) -> None:
    """No-op — buckets removed in v2. Kept for backward compat."""
    pass


# ══════════════════════════════════════════════════════════════
# ⭐ CORE: MATCH COMPATIBILITY CHECK
# ══════════════════════════════════════════════════════════════

def _get_effective_pref(user: dict) -> str:
    """Return user's effective gender preference.

    ⭐ RULE: Only VIP users can have a real pref.
    Non-VIP users ALWAYS get "Any" regardless of what's stored.
    """
    if not user.get("is_vip"):
        return "Any"
    pref = user.get("pref_gender", "Any")
    if pref not in ("Male", "Female"):
        return "Any"
    return pref


def _can_match(user_a: dict, user_b: dict) -> bool:
    """Check if two users can be matched.

    ⭐ SIMPLE RULE:
      - User A's pref must accept User B's gender (or A's pref is "Any")
      - User B's pref must accept User A's gender (or B's pref is "Any")
    """
    if not user_a or not user_b:
        return False
    if user_a.get("user_id") == user_b.get("user_id"):
        return False
    if user_a.get("is_banned") or user_b.get("is_banned"):
        return False
    if user_a.get("state") != "SEARCHING" or user_b.get("state") != "SEARCHING":
        return False
    if user_a.get("partner") or user_b.get("partner"):
        return False

    pref_a = _get_effective_pref(user_a)
    pref_b = _get_effective_pref(user_b)
    gender_a = user_a.get("gender")
    gender_b = user_b.get("gender")

    if pref_a != "Any":
        if gender_b not in ("Male", "Female"):
            return False
        if gender_b != pref_a:
            return False

    if pref_b != "Any":
        if gender_a not in ("Male", "Female"):
            return False
        if gender_a != pref_b:
            return False

    if user_b.get("user_id") in (user_a.get("blocked_users") or []):
        return False
    if user_a.get("user_id") in (user_b.get("blocked_users") or []):
        return False

    if not ALLOW_INSTANT_REMATCH:
        if user_b.get("user_id") in (user_a.get("recent_partners") or []):
            return False
        if user_a.get("user_id") in (user_b.get("recent_partners") or []):
            return False

    return True


def _find_partner(uid: int) -> int | None:
    """Find a compatible partner for user in queue."""
    me = users.get(uid)
    if not me or me.get("state") != "SEARCHING" or me.get("partner"):
        return None

    candidates = []
    for other_id in queue_set:
        if other_id == uid:
            continue
        other = users.get(other_id)
        if not other:
            continue
        if not _can_match(me, other):
            continue
        candidates.append(other_id)

    if not candidates:
        return None

    if VIP_PRIORITY_QUEUE and not me.get("is_vip"):
        vip_candidates = [c for c in candidates if users.get(c, {}).get("is_vip")]
        if vip_candidates:
            return vip_candidates[0]

    return candidates[0]


# ══════════════════════════════════════════════════════════════
# UI BUILDERS
# ══════════════════════════════════════════════════════════════

def _estimate_wait(waiting: int) -> int:
    if analytics.get("match_wait_count", 0) >= 5:
        avg = analytics["match_wait_total"] / analytics["match_wait_count"]
        avg = min(avg, 8.0)
    else:
        avg = 5.0
    est = avg if waiting <= 1 else avg / (waiting ** 0.5)
    return max(3, min(int(est), 15))


def _build_searching_card(name: str, waiting: int, me: dict) -> str:
    est_wait = _estimate_wait(waiting)
    safe_name = esc(name)
    return (
        "🔍  ✨  Searching...  ✨  🔍\n"
        "▎\n"
        f"▎ 👋 Hey <b>{safe_name}</b>, finding your match...\n"
        "▎\n"
        "▎ ⏳ <b>Status</b>\n"
        "▎   ├ 🔍 In queue\n"
        f"▎   ├ 👥 Waiting : <b>{waiting}</b> users\n"
        f"▎   └ ⚡️ Est. time : <b>~{est_wait}s</b>\n"
        "▎\n"
        "▎ 💡 <b>Tip</b>\n"
        "▎   └ Send /cancel to stop searching"
    )


def _build_already_searching_card(name: str, waiting: int) -> str:
    est_wait = _estimate_wait(waiting)
    safe_name = esc(name)
    return (
        "🔍  ✨  <b>Already Searching</b>  ✨  🔍\n"
        "▎\n"
        f"▎ 👋 Hey <b>{safe_name}</b>, you're already in queue...\n"
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
    safe_name = esc(name)
    if me and me.get("is_vip"):
        tip = "💡 VIP unlocked: priority queue + gender filter 🎯"
    else:
        tip = "💡 Ready for a fresh match anytime!"
    return (
        "🛑  ✨  <b>Search Cancelled</b>  ✨  🛑\n"
        "▎\n"
        f"▎ ✅ Hey <b>{safe_name}</b>, search stopped.\n"
        "▎\n"
        "▎ 🎯  <b>What's next</b>\n"
        "▎   ├ 🎲 /next — Search again\n"
        "▎   ├ ⚙️ /settings — Preferences\n"
        "▎   └ 🛍️ /buy — Unlock VIP\n"
        "▎\n"
        f"<i>{tip}</i>"
    )


def _pick_theme(me: dict) -> dict:
    chosen = me.get("theme")
    if chosen and chosen in THEMES:
        return THEMES[chosen]
    if me.get("is_vip"):
        return THEMES["royal"]
    keys = list(THEMES.keys())[:5]
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
        gender_line = {"Female": "👩 Female", "Male": "👨 Male"}.get(g, f"👤 {esc(g)}")
        block_line = "🚫 /block  — Block & skip"
        voice_line = "🎙️ /voice  — Anonymous voice call"
    else:
        vip_link = f"https://t.me/{BOT_USERNAME}?start=vip"
        gender_line = f'🔒 Hidden  <a href="{vip_link}">👑 VIP Only</a>'
        block_line = f'🚫 /block  — <a href="{vip_link}">👑 VIP Only</a>'
        voice_line = f'🎙️ /voice  — <a href="{vip_link}">👑 VIP Only</a>'

    safe_common = ", ".join(esc(c) for c in common) if common else "—"
    title = f"{theme['tl']}  {mid}  <b>Chat Connected!</b>  {mid}  {theme['tr']}"
    tip_line = _get_tip(me)

    return "\n".join([
        title,
        "▎",
        f"▎ <b>{theme['p']}  Partner Info</b>",
        f"▎   ├ 🚻 Gender : {gender_line}",
        f"▎   └ 🎯 Common : {safe_common}",
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
    ])


def _cancel_search_keyboard():
    return InlineKeyboardMarkup([[
        ikb("❌ ᴄᴀɴᴄᴇʟ ꜱᴇᴀʀᴄʜ",
            callback_data="CANCEL_SEARCH",
            style="danger"),
    ]])


def _build_end_keyboard(u: dict, partner_id: int = None):
    kb_rows = []
    if u.get("is_vip") and partner_id:
        kb_rows.append([
            ikb("🔄 Reconnect 👑", callback_data=f"RECONNECT_PID:{partner_id}"),
            ikb("💾 Save Partner 👑", callback_data=f"SAVE_PARTNER_PID:{partner_id}"),
        ])
    kb_rows.append([
        ikb("🎲 Next", callback_data="START_NEXT"),
        ikb("🏠 Dashboard", callback_data="BACK_DASHBOARD"),
    ])
    return InlineKeyboardMarkup(kb_rows)


# ══════════════════════════════════════════════════════════════
# ⭐ ENQUEUE / DEQUEUE HELPERS
# ══════════════════════════════════════════════════════════════

def _enqueue_user(uid: int) -> None:
    """Add user to queue. MUST be called inside queue_lock."""
    if uid in queue_set:
        return
    if VIP_PRIORITY_QUEUE and users.get(uid, {}).get("is_vip"):
        queue.appendleft(uid)
    else:
        queue.append(uid)
    queue_set.add(uid)
    u = users.get(uid)
    if u:
        u["state"] = "SEARCHING"


def _dequeue_user(uid: int) -> None:
    """Remove user from queue. MUST be called inside queue_lock."""
    queue_set.discard(uid)
    try:
        queue.remove(uid)
    except ValueError:
        pass


async def _delete_searching_card(context, uid: int):
    u = users.get(uid)
    if not u:
        return
    msg_id = u.get("_search_msg_id")
    if not msg_id:
        return
    try:
        await context.bot.delete_message(chat_id=uid, message_id=msg_id)
    except Exception:
        pass
    u["_search_msg_id"] = None


# ══════════════════════════════════════════════════════════════
# ⭐ CONNECT USERS
# ══════════════════════════════════════════════════════════════

async def connect_users(context, uid1: int, uid2: int) -> bool:
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return False

    from database import _do_save

    await asyncio.gather(
        _delete_searching_card(context, uid1),
        _delete_searching_card(context, uid2),
        return_exceptions=True,
    )

    for uid, partner_id in ((uid1, uid2), (uid2, uid1)):
        u = users.get(uid)
        if not u:
            continue
        u["partner"] = partner_id
        u["state"] = "CHAT"
        u["pending_media"] = {}
        u["total_matches"] = u.get("total_matches", 0) + 1
        u["total_chats"] = u.get("total_chats", 0) + 1
        u["_last_partner_id"] = partner_id

    async def _persist(uid: int) -> bool:
        u = users.get(uid)
        if not u:
            return False
        for attempt in range(5):
            try:
                ok = await _do_save(uid, u, pop_queue=True)
                if ok:
                    return True
            except Exception as e:
                logger.error(f"Persist {uid} attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.3 * (attempt + 1))
        logger.critical(f"🚨 Failed to persist {uid}")
        return False

    ok1, ok2 = await asyncio.gather(
        _persist(uid1), _persist(uid2),
        return_exceptions=False,
    )

    for uid in (uid1, uid2):
        u = users.get(uid)
        if u and not u.get("is_vip"):
            if u.get("pref_gender") != "Any":
                u["pref_gender"] = "Any"
                u["pending_pref_from_credit"] = False

    analytics["matches_today"] += 1
    for uid in (uid1, uid2):
        started = last_next_time.get(uid)
        if started:
            waited = utcnow().timestamp() - started
            if 0 < waited < 600:
                analytics["match_wait_total"] += waited
                analytics["match_wait_count"] += 1

    try:
        from services.achievements import unlock_achievement
        for uid in (uid1, uid2):
            u = users.get(uid)
            if not u:
                continue
            if u["total_matches"] == 1:
                asyncio.create_task(unlock_achievement(context, uid, "first_match"))
            elif u["total_matches"] == 10:
                asyncio.create_task(unlock_achievement(context, uid, "ten_matches"))
            elif u["total_matches"] == 50:
                asyncio.create_task(unlock_achievement(context, uid, "fifty_matches"))
    except Exception:
        pass

    common = list(set(u1.get("interests", [])) & set(u2.get("interests", [])))
    card1 = _build_connect_card(u1, u2, common)
    card2 = _build_connect_card(u2, u1, common)
    kb1 = get_chat_keyboard(u1)
    kb2 = get_chat_keyboard(u2)

    results = await asyncio.gather(
        safe_send(context, uid1, card1, reply_markup=kb1,
                  parse_mode="HTML", disable_web_page_preview=True),
        safe_send(context, uid2, card2, reply_markup=kb2,
                  parse_mode="HTML", disable_web_page_preview=True),
        return_exceptions=True,
    )

    if results[0] is None or isinstance(results[0], Exception):
        await asyncio.sleep(0.5)
        await safe_send(context, uid1, card1, reply_markup=kb1,
                        parse_mode="HTML", disable_web_page_preview=True)
    if results[1] is None or isinstance(results[1], Exception):
        await asyncio.sleep(0.5)
        await safe_send(context, uid2, card2, reply_markup=kb2,
                        parse_mode="HTML", disable_web_page_preview=True)

    return ok1 and ok2


# ══════════════════════════════════════════════════════════════
# ⭐ TRY_MATCH
# ══════════════════════════════════════════════════════════════

async def try_match(context, uid: int):
    u = await get_user(uid)
    if not u or u.get("is_banned"):
        return

    name = u.get("name") or "there"
    safe_name = esc(name)

    if u.get("state") == "MATCHING":
        return await safe_send(
            context, uid,
            box_simple("Please Wait", "⏳ Finalizing your match...", emoji="⏳"),
            parse_mode="HTML",
        )

    if state.maintenance_mode and not await is_owner_or_admin(uid):
        return await safe_send(
            context, uid,
            "🚧  ✨  <b>Under Maintenance</b>  ✨  🚧\n"
            "▎\n"
            "▎ 🛠️ We're upgrading SparkTalks.\n"
            "▎ ⏰ Please try again in a few minutes.",
            parse_mode="HTML",
        )

    if await should_prompt_join(context, u, uid):
        await prompt_join(context, uid)
        return

    if not u.get("gender"):
        kb_markup = InlineKeyboardMarkup([[
            ikb("👨🏻 Male", callback_data="G_MALE"),
            ikb("👩🏻 Female", callback_data="G_FEMALE"),
        ]])
        return await safe_send(
            context, uid,
            box_card("Setup Required", [
                {"type": "text", "content": f"⚠️ Hey {safe_name}, please set your gender first."},
                {"type": "divider"},
                {"type": "text", "content": "Select below 👇"},
            ], emoji="⚠️"),
            reply_markup=kb_markup, parse_mode="HTML",
        )

    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        u["pref_gender"] = "Any"
        u["pending_pref_from_credit"] = False
        await save_user_to_db(uid, u)
        logger.info(f"Auto-reset pref_gender for non-VIP {uid}")

    async with queue_lock:
        already_searching = (uid in queue_set and u.get("state") == "SEARCHING")

    if already_searching:
        await _delete_searching_card(context, uid)
        waiting = len(queue)
        body = _build_already_searching_card(name, waiting)
        sent = await safe_send(context, uid, body,
                                reply_markup=_cancel_search_keyboard(),
                                parse_mode="HTML")
        if sent:
            u["_search_msg_id"] = sent.message_id
        return

    if not u.get("is_vip") and NEXT_COOLDOWN_SECONDS > 0:
        now_ts = utcnow().timestamp()
        last = last_next_time.get(uid, 0)
        if now_ts - last < NEXT_COOLDOWN_SECONDS:
            remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last)) + 1
            return await safe_send(
                context, uid,
                box_simple("Please Wait",
                           f"⏳ Hey {safe_name}, wait {remaining}s before searching again.",
                           emoji="⏳"),
                parse_mode="HTML",
            )
    last_next_time[uid] = utcnow().timestamp()

    if u.get("partner"):
        await disconnect(context, uid, u["partner"],
                         requeue=True, ender_id=uid, notify_ender=False)
        return

    partner_id = None
    async with queue_lock:
        u = users.get(uid)
        if not u or u.get("partner") or u.get("state") == "CHAT":
            return
        partner_id = _find_partner(uid)

        if partner_id is not None:
            _dequeue_user(uid)
            _dequeue_user(partner_id)
            u["state"] = "MATCHING"
            p = users.get(partner_id)
            if p:
                p["state"] = "MATCHING"
        else:
            _enqueue_user(uid)

    if partner_id is not None:
        logger.info(f"⚡ Instant match: {uid} ↔ {partner_id}")
        try:
            await connect_users(context, uid, partner_id)
        except Exception as e:
            logger.error(f"connect_users failed ({uid},{partner_id}): {e}", exc_info=True)
            async with queue_lock:
                for x in (uid, partner_id):
                    ux = users.get(x)
                    if ux and ux.get("state") == "MATCHING":
                        ux["state"] = "IDLE"
                        ux["partner"] = None
        return

    async with queue_lock:
        waiting = len(queue)
    body = _build_searching_card(name, waiting, u)
    sent = await safe_send(context, uid, body,
                            reply_markup=_cancel_search_keyboard(),
                            parse_mode="HTML")
    if sent:
        u["_search_msg_id"] = sent.message_id


# ══════════════════════════════════════════════════════════════
# ⭐ BACKGROUND_MATCHER
# ══════════════════════════════════════════════════════════════

async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    if state.maintenance_mode:
        return

    if len(queue_set) < 2:
        return

    pairs = []

    async with queue_lock:
        waiting = [uid for uid in queue if uid in queue_set]
        matched = set()

        for uid in waiting:
            if uid in matched:
                continue
            u = users.get(uid)
            if not u or u.get("state") != "SEARCHING" or u.get("partner"):
                continue

            for other_id in waiting:
                if other_id == uid or other_id in matched:
                    continue
                other = users.get(other_id)
                if not other:
                    continue
                if not _can_match(u, other):
                    continue

                pairs.append((uid, other_id))
                matched.add(uid)
                matched.add(other_id)
                break

        for a, b in pairs:
            _dequeue_user(a)
            _dequeue_user(b)
            ua = users.get(a)
            ub = users.get(b)
            if ua:
                ua["state"] = "MATCHING"
            if ub:
                ub["state"] = "MATCHING"

    if not pairs:
        return

    async def _safe_connect(a: int, b: int):
        try:
            await connect_users(context, a, b)
        except Exception as e:
            logger.error(f"connect_users failed ({a},{b}): {e}", exc_info=True)
            async with queue_lock:
                for uid in (a, b):
                    ux = users.get(uid)
                    if ux and ux.get("state") == "MATCHING":
                        ux["state"] = "IDLE"
                        ux["partner"] = None

    await asyncio.gather(
        *[_safe_connect(a, b) for a, b in pairs],
        return_exceptions=True,
    )

    logger.info(f"⚡ Background matched {len(pairs)} pairs")


# ══════════════════════════════════════════════════════════════
# ⭐ DISCONNECT
# ══════════════════════════════════════════════════════════════

async def disconnect(context, u1: int, u2: int, requeue: bool = False,
                     ender_id: int = None, notify_ender: bool = True):
    from database import _do_save

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
        try:
            await _delete_searching_card(context, uid)
        except Exception:
            pass
        try:
            await _do_save(uid, u, pop_queue=True)
        except Exception as e:
            logger.error(f"disconnect persist {uid} failed: {e}")

    try:
        from state import pending_media_timeouts
        for k in list(pending_media_timeouts.keys()):
            if k[0] in (u1, u2):
                pending_media_timeouts.pop(k, None)
    except Exception:
        pass

    try:
        from state import active_voice_rooms
        for rid, r in list(active_voice_rooms.items()):
            if {r["u1"], r["u2"]} == {u1, u2}:
                active_voice_rooms.pop(rid, None)
    except Exception:
        pass

    for uid in (u1, u2):
        if ender_id is not None and uid == ender_id and not notify_ender:
            continue

        if ender_id is not None and uid == ender_id:
            reason = "You ended the chat."
        elif ender_id is not None:
            reason = "Your partner ended the chat."
        else:
            reason = "The conversation was closed."

        partner_id = u2 if uid == u1 else u1
        u = users.get(uid)
        if u:
            u["_last_partner_id"] = partner_id

        is_banned = bool(u.get("is_banned")) if u else False

        body = (
            f"🛑  ✨  <b>Chat Ended</b>  ✨  🛑\n"
            f"▎\n"
            f"▎ 💔 <b>{reason}</b>\n"
            f"▎\n"
        )
        if u and u.get("is_vip") and not is_banned:
            body += (
                "▎ 👑 <b>VIP Options</b>\n"
                "▎   ├ 🔄 Reconnect — Same partner\n"
                "▎   └ 💾 Save — Add to saved list\n"
                "▎\n"
            )
        body += (
            f"▎ 🔄 <b>Next Steps</b>\n"
            f"▎   ├ 🎲 /next — Find new partner\n"
            f"▎   ├ 👤 /profile — View your stats\n"
            f"▎   └ 🏠 /start — Back to dashboard\n"
            f"▎\n"
            f"▎ 💡 Ready to meet someone new? 👇"
        )

        kb_markup = None if is_banned else (_build_end_keyboard(u, partner_id) if u else None)
        await safe_send(context, uid, body, parse_mode="HTML", reply_markup=kb_markup)

    if requeue:
        u = users.get(u1)
        if u and not u.get("is_banned") and not u.get("partner"):
            async with queue_lock:
                _enqueue_user(u1)
                waiting = len(queue)
            name = u.get("name") or "there"
            body = _build_searching_card(name, waiting, u)
            sent = await safe_send(context, u1, body,
                                    reply_markup=_cancel_search_keyboard(),
                                    parse_mode="HTML")
            if sent:
                u["_search_msg_id"] = sent.message_id


# ══════════════════════════════════════════════════════════════
# ⭐ CANCEL SEARCH
# ══════════════════════════════════════════════════════════════

async def cancel_search(context, uid: int):
    u = await get_user(uid)
    if not u:
        return
    name = u.get("name") or "there"

    await _delete_searching_card(context, uid)

    async with queue_lock:
        _dequeue_user(uid)

    if u.get("state") in ("SEARCHING", "MATCHING"):
        u["state"] = "IDLE"

    body = _build_cancel_card(name, u)
    await safe_send(context, uid, body, parse_mode="HTML",
                    reply_markup=get_main_keyboard())


# ══════════════════════════════════════════════════════════════
# ⭐ END CHAT
# ══════════════════════════════════════════════════════════════

async def end_chat_internal(context, uid: int):
    u = await get_user(uid)
    name = esc(u.get("name") if u else "there")

    if not u or (not u.get("partner")
                 and u.get("state") not in ("SEARCHING", "MATCHING")):
        return await safe_send(
            context, uid,
            box_simple("Notice",
                       f"⚠️ Hey {name}, you are not in a chat or search.",
                       emoji="⚠️"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )

    if u.get("state") in ("SEARCHING", "MATCHING"):
        await cancel_search(context, uid)
        return

    await disconnect(context, uid, u["partner"], ender_id=uid)


# ══════════════════════════════════════════════════════════════
# ⭐ REPORT
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

    partner = users.get(partner_id)
    if not partner:
        partner = await get_user(partner_id)
    if partner:
        partner["report_count"] = partner.get("report_count", 0) + 1
        await save_user_to_db(partner_id, partner)

    try:
        from services.moderation import log_report
        await log_report(context, uid, partner_id, reason=None)
    except Exception as e:
        logger.error(f"log_report failed: {e}")

    try:
        await log_event(
            context, "REPORT",
            category="user", level="warn",
            actor_id=uid, target_id=partner_id,
            details={"Channel": "logged for audit"},
        )
    except Exception:
        pass

    user_body = (
        f"🚨  ✨  <b>Report Sent</b>  ✨  🚨\n"
        f"▎\n"
        f"▎ ✅ Thank you for reporting!\n"
        f"▎\n"
        f"▎ 🔒 Partner NOT notified\n"
        f"▎ 💬 Chat continues\n"
        f"▎ 👮 Admins notified <b>instantly</b>\n"
        f"▎\n"
        f"▎ 💡 Use /block to stop this user (VIP)"
    )
    await safe_send(context, uid, user_body, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════
# ⭐ BLOCK
# ══════════════════════════════════════════════════════════════

async def block_internal(context, uid: int):
    from config import BLOCK_REQUIRES_VIP, MAX_BLOCKED_USERS

    u = await get_user(uid)
    if not u:
        return
    if BLOCK_REQUIRES_VIP and not u.get("is_vip"):
        kb_markup = InlineKeyboardMarkup([[
            ikb("🛍️ Get VIP", callback_data="BUY_STORE"),
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
        return await safe_send(context, uid, body, reply_markup=kb_markup, parse_mode="HTML")

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

    saved = u.get("saved_partner_ids") or []
    if partner_id in saved:
        nicks = u.get("saved_partner_nicks") or []
        idx = saved.index(partner_id)
        saved.pop(idx)
        if idx < len(nicks):
            nicks.pop(idx)
        u["saved_partner_ids"] = saved
        u["saved_partner_nicks"] = nicks

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

    try:
        await log_event(
            context, "BLOCK",
            category="user", level="info",
            actor_id=uid, target_id=partner_id,
            details={"Total blocked": total},
        )
    except Exception:
        pass