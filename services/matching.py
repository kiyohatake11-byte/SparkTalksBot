"""Matching service — 6 optimizations for maximum speed.

⚡ OPTIMIZATIONS:
  1. Parallel connect_users (DB writes + card sends via asyncio.gather)
  2. Parallel pairs in background_matcher (bulk connect)
  3. Instant matcher on enqueue (_try_instant_match)
  4. Bucketed queue (O(1) lookup instead of O(n²) scan)
  5. Reduced cooldown (2s → 1s in config)
  6. Parallel flush_writes (handled in database.py)
"""
import asyncio
import random
import logging
from telegram import InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    NEXT_COOLDOWN_SECONDS, MAX_RECENT_PARTNERS, MAX_BLOCKED_USERS,
    ALLOW_INSTANT_REMATCH, BLOCK_REQUIRES_VIP, BOT_USERNAME,
    VIP_PRIORITY_QUEUE,
)
from state import (
    users, queue, queue_set, queue_lock, last_next_time, admin_cache,
    analytics, queue_buckets,
)
import state
from database import get_user, save_user_to_db, is_owner_or_admin
from utils import box_card, box_simple, safe_send, utcnow, to_bold, esc, ikb, log_event
from keyboards import get_main_keyboard, get_chat_keyboard
from services.community import should_prompt_join, prompt_join

logger = logging.getLogger("sparktalks")


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
    "💡 Tired of 1s wait? VIP = instant next! 🚀",
    "💡 Stand out with 👑 VIP badge in /profile!",
    "💡 VIP users see partner's gender instantly! 👀",
    "💡 Skip wrong matches — VIP gender filter! 🎯",
    "💡 VIP = priority matching in queue! ⚡",
    "💡 Starting at just ₹149 → 14 days VIP! → /buy",
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
    "💡 Reconnect & Save Partner — VIP-only perks! 💾",
]


# ══════════════════════════════════════════════════════════════
# ⭐ OPTIMIZATION #4: Bucket Helpers (O(1) lookup)
# ══════════════════════════════════════════════════════════════

def _add_to_bucket(uid: int, u: dict) -> None:
    """Add user to their pref/gender bucket. Idempotent."""
    if not u or not isinstance(u, dict):
        return
    pref = u.get("pref_gender", "Any")
    if pref not in ("Male", "Female", "Any"):
        pref = "Any"
    gender = u.get("gender", "Any")
    if gender not in ("Male", "Female", "Any"):
        gender = "Any"
    if pref not in queue_buckets:
        queue_buckets[pref] = {"Any": set(), "Male": set(), "Female": set()}
    queue_buckets[pref][gender].add(uid)


def _remove_from_bucket_by_uid(uid: int) -> None:
    """Remove user from all buckets. Safe to call even if not present."""
    for pref_bucket in queue_buckets.values():
        for gender_set in pref_bucket.values():
            gender_set.discard(uid)


def _find_match_from_buckets(uid: int, u: dict, exclude: set = None):
    """O(1)-ish match lookup via buckets.

    Match condition:
      - candidate.gender == u.pref_gender  (or u.pref_gender == "Any")
      - candidate.pref_gender == u.gender  (or candidate.pref == "Any")

    Returns candidate_id or None. Does NOT modify queue/buckets — caller does.
    """
    if exclude is None:
        exclude = set()

    my_pref = u.get("pref_gender", "Any")
    if my_pref not in ("Male", "Female", "Any"):
        my_pref = "Any"
    my_gender = u.get("gender", "Any")
    if my_gender not in ("Male", "Female", "Any"):
        my_gender = "Any"

    # Target genders: which genders match my preference
    if my_pref == "Any":
        target_genders = ["Male", "Female", "Any"]
    else:
        target_genders = [my_pref, "Any"]

    # Candidate prefs: which prefs accept my gender
    if my_gender == "Any":
        candidate_prefs = ["Male", "Female", "Any"]
    else:
        candidate_prefs = [my_gender, "Any"]

    stale_to_remove = []

    for c_pref in candidate_prefs:
        pref_bucket = queue_buckets.get(c_pref)
        if not pref_bucket:
            continue
        for c_gender in target_genders:
            candidates = pref_bucket.get(c_gender, set())
            if not candidates:
                continue
            for cid in list(candidates):
                if cid == uid or cid in exclude:
                    continue

                # Self-heal: if not in queue_set, prune from bucket
                if cid not in queue_set:
                    stale_to_remove.append(cid)
                    continue

                c = users.get(cid)
                if not c:
                    stale_to_remove.append(cid)
                    continue
                if c.get("is_banned") or c.get("state") != "SEARCHING" or c.get("partner"):
                    continue
                if _is_recent(u, cid) or _is_recent(c, uid):
                    continue
                if _is_blocked(u, cid) or _is_blocked(c, uid):
                    continue

                # Prune accumulated stale entries before returning
                for sid in stale_to_remove:
                    _remove_from_bucket_by_uid(sid)
                return cid

    for sid in stale_to_remove:
        _remove_from_bucket_by_uid(sid)
    return None


def _estimate_wait(waiting: int) -> int:
    if analytics["match_wait_count"] >= 5:
        avg = analytics["match_wait_total"] / analytics["match_wait_count"]
        avg = min(avg, 8.0)
    else:
        avg = 5.0
    est = avg if waiting <= 1 else avg / (waiting ** 0.5)
    return max(3, min(int(est), 15))


def _build_searching_card(name: str, waiting: int, me: dict,
                          est_wait: int = None) -> str:
    if est_wait is None:
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


def _build_already_searching_card(name: str, waiting: int,
                                   est_wait: int = None) -> str:
    if est_wait is None:
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


def _is_recent(u: dict, candidate_id: int) -> bool:
    if ALLOW_INSTANT_REMATCH or MAX_RECENT_PARTNERS <= 0:
        return False
    return candidate_id in set(u.get("recent_partners", []))


def _is_blocked(u: dict, candidate_id: int) -> bool:
    return candidate_id in set(u.get("blocked_users", []))


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


def _cancel_search_keyboard():
    return InlineKeyboardMarkup([[
        ikb("❌ ᴄᴀɴᴄᴇʟ ꜱᴇᴀʀᴄʜ",
            callback_data="CANCEL_SEARCH",
            style="danger"),
    ]])


# ══════════════════════════════════════════════════════════════
# DISCONNECT — unchanged (already fast)
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
        for attempt in range(3):
            try:
                ok = await _do_save(uid, u, pop_queue=True)
                if ok:
                    logger.info(f"💾 Cleared chat for {uid} in DB (attempt {attempt+1})")
                    break
            except Exception as e:
                logger.error(f"disconnect persist {uid} attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.3 * (attempt + 1))

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

        if is_banned:
            kb_markup = None
        else:
            kb_markup = _build_end_keyboard(u, partner_id) if u else None

        await safe_send(context, uid, body, parse_mode="HTML", reply_markup=kb_markup)

    if requeue:
        u = users.get(u1)
        if u and not u.get("is_banned") and not u.get("partner"):
            u["state"] = "SEARCHING"
            async with queue_lock:
                if u1 not in queue_set:
                    if VIP_PRIORITY_QUEUE and u.get("is_vip"):
                        queue.appendleft(u1)
                    else:
                        queue.append(u1)
                    queue_set.add(u1)
                    _add_to_bucket(u1, u)   # ⭐ OPT #4
                waiting = len(queue)
            name = u.get("name") or "there"
            body = _build_searching_card(name, waiting, u)
            sent = await safe_send(context, u1, body,
                                    reply_markup=_cancel_search_keyboard(),
                                    parse_mode="HTML")
            if sent:
                u["_search_msg_id"] = sent.message_id


# ══════════════════════════════════════════════════════════════
# ⭐ OPTIMIZATION #1: Parallel connect_users
# ══════════════════════════════════════════════════════════════
async def connect_users(context, uid1: int, uid2: int) -> bool:
    """Connect two users — PARALLEL DB writes + PARALLEL card sends.

    ⚡ OPT #1: Sequential ~700ms → parallel ~350ms (~2x faster)
    """
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return False

    from database import _do_save

    # Parallel: delete searching cards (independent)
    await asyncio.gather(
        _delete_searching_card(context, uid1),
        _delete_searching_card(context, uid2),
        return_exceptions=True,
    )

    # Update memory instantly
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

    # ⚡ Parallel DB writes
    async def _persist_with_retry(uid: int, partner_id: int, max_retries: int = 5) -> bool:
        u = users.get(uid)
        if not u:
            return False
        for attempt in range(max_retries):
            try:
                ok = await _do_save(uid, u, pop_queue=True)
                if ok:
                    logger.info(f"💾 Persisted chat {uid} ↔ {partner_id} (attempt {attempt+1})")
                    return True
            except Exception as e:
                logger.error(f"❌ Persist chat {uid} attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.3 * (attempt + 1))
        logger.critical(f"🚨 CRITICAL: Failed to persist {uid} ↔ {partner_id}")
        return False

    ok1, ok2 = await asyncio.gather(
        _persist_with_retry(uid1, uid2),
        _persist_with_retry(uid2, uid1),
        return_exceptions=False,
    )

    if not ok1 or not ok2:
        logger.error(f"⚠️ Chat {uid1}↔{uid2} may NOT survive restart!")

    # Post-connect logic (fast, memory-only)
    for uid, partner_id in ((uid1, uid2), (uid2, uid1)):
        u = users.get(uid)
        if not u:
            continue

        if not u.get("is_vip"):
            if u.get("pending_pref_from_credit") and u.get("gender_pref_credits", 0) > 0:
                u["gender_pref_credits"] = max(0, u["gender_pref_credits"] - 1)
                remaining = u["gender_pref_credits"]
                # Fire-and-forget credit notification
                asyncio.create_task(safe_send(
                    context, uid,
                    f"🎯  ✨  <b>Gender Filter Used</b>  ✨  🎯\n"
                    f"▎\n▎ ✅ 1 credit consumed\n"
                    f"▎ 🎁 Remaining credits : <b>{remaining}</b>\n"
                    f"▎\n💡 <i>Enjoy your match!</i>",
                    parse_mode="HTML",
                ))
            u["pref_gender"] = "Any"
            u["pending_pref_from_credit"] = False

        started = last_next_time.get(uid)
        if started:
            waited = utcnow().timestamp() - started
            if 0 < waited < 600:
                analytics["match_wait_total"] += waited
                analytics["match_wait_count"] += 1

        # Fire-and-forget achievements
        try:
            from services.achievements import unlock_achievement
            if u["total_matches"] == 1:
                asyncio.create_task(unlock_achievement(context, uid, "first_match"))
            if u["total_matches"] == 10:
                asyncio.create_task(unlock_achievement(context, uid, "ten_matches"))
            if u["total_matches"] == 50:
                asyncio.create_task(unlock_achievement(context, uid, "fifty_matches"))
            if u["total_chats"] == 100:
                asyncio.create_task(unlock_achievement(context, uid, "hundred_chats"))
        except Exception:
            pass

    analytics["matches_today"] += 1

    # ⚡ Parallel card sends with fallback retry
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

    # Fallback: retry any that failed
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
# ⭐ OPTIMIZATION #3: Instant matcher on enqueue
# ══════════════════════════════════════════════════════════════
async def _try_instant_match(context, uid: int):
    """Try to match a single user immediately after enqueueing.

    ⚡ OPT #3: O(1) bucket lookup instead of full O(n²) background scan.
    """
    await asyncio.sleep(0.05)  # Tiny delay to let lock settle

    u = users.get(uid)
    if not u or u.get("state") != "SEARCHING" or u.get("partner"):
        return

    found = None
    async with queue_lock:
        u = users.get(uid)
        if not u or u.get("state") != "SEARCHING" or u.get("partner"):
            return

        found = _find_match_from_buckets(uid, u)

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
            _remove_from_bucket_by_uid(found)
            _remove_from_bucket_by_uid(uid)
            u["state"] = "MATCHING"
            if found in users:
                users[found]["state"] = "MATCHING"

    if found is not None:
        try:
            await connect_users(context, uid, found)
        except Exception as e:
            logger.error(f"Instant match failed ({uid},{found}): {e}", exc_info=True)
            for x in (uid, found):
                ux = users.get(x)
                if ux and ux.get("state") == "MATCHING":
                    ux["state"] = "IDLE"


# ══════════════════════════════════════════════════════════════
# TRY_MATCH — with instant matcher trigger
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

    if not u.get("is_vip") and NEXT_COOLDOWN_SECONDS > 0:
        now_ts = utcnow().timestamp()
        last = last_next_time.get(uid, 0)
        if now_ts - last < NEXT_COOLDOWN_SECONDS:
            remaining = int(NEXT_COOLDOWN_SECONDS - (now_ts - last))
            return await safe_send(
                context, uid,
                box_simple("Please Wait",
                           f"⏳ Hey {safe_name}, wait {remaining}s before searching again.",
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
        # ⭐ Re-add to bucket in case it got out of sync
        _add_to_bucket(uid, u)
        await _delete_searching_card(context, uid)
        waiting = len(queue)
        body = _build_already_searching_card(name, waiting)
        sent = await safe_send(context, uid, body,
                                reply_markup=_cancel_search_keyboard(),
                                parse_mode="HTML")
        if sent:
            u["_search_msg_id"] = sent.message_id
        return

    # Free-user gender pref handling
    if not u.get("is_vip") and u.get("pref_gender") != "Any":
        if u.get("pending_pref_from_credit") and u.get("gender_pref_credits", 0) > 0:
            remaining = u["gender_pref_credits"]
            await safe_send(
                context, uid,
                f"🎯  ✨  <b>Gender Filter Active</b>  ✨  🎯\n"
                f"▎\n"
                f"▎ Filter : <b>{esc(u['pref_gender'])} only</b>\n"
                f"▎ Credits : <b>{remaining}</b> (used on match)\n"
                f"▎\n"
                f"💡 <i>1 credit will be deducted when matched.</i>",
                parse_mode="HTML",
            )
        else:
            u["pref_gender"] = "Any"
            u["pending_pref_from_credit"] = False
            await save_user_to_db(uid, u)
            kb_markup = InlineKeyboardMarkup([
                [ikb("🛍️ Get VIP", callback_data="BUY_STORE")],
                [ikb("🎁 Free VIP (Invite)", callback_data="OPEN_INVITE")],
                [ikb("⚙️ Settings", callback_data="OPEN_SETTINGS")],
            ])
            body = box_card("VIP Needed", [
                {"type": "text", "content": "⚠️ Gender filter is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content":
                    "💡 Earn free uses:\n"
                    "  ├ 3 invites  → 1× Filter\n"
                    "  └ 5 invites  → 2× Filter"},
                {"type": "divider"},
                {"type": "text", "content": "Preference reset to Any."},
            ], emoji="👑")
            return await safe_send(context, uid, body, reply_markup=kb_markup, parse_mode="HTML")

    # ⭐ OPT #4: Bucket lookup on first join
    found = None
    async with queue_lock:
        found = _find_match_from_buckets(uid, u)
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
            _remove_from_bucket_by_uid(found)
            _remove_from_bucket_by_uid(uid)
            if found in users:
                users[found]["state"] = "MATCHING"
            u["state"] = "MATCHING"

    if found is not None:
        try:
            await connect_users(context, uid, found)
        except Exception as e:
            logger.error(f"connect_users failed in try_match ({uid},{found}): {e}", exc_info=True)
            for x in (uid, found):
                ux = users.get(x)
                if ux and ux.get("state") == "MATCHING":
                    ux["state"] = "IDLE"
        return

    # Guard: user got partner while waiting
    u = users.get(uid)
    if not u or u.get("partner") or u.get("state") == "CHAT":
        return

    # Enqueue + add to bucket
    async with queue_lock:
        if uid not in queue_set:
            if VIP_PRIORITY_QUEUE and u.get("is_vip"):
                queue.appendleft(uid)
            else:
                queue.append(uid)
            queue_set.add(uid)
            _add_to_bucket(uid, u)   # ⭐ OPT #4
        u["state"] = "SEARCHING"
        waiting = len(queue)

    body = _build_searching_card(name, waiting, u)
    sent = await safe_send(context, uid, body,
                            reply_markup=_cancel_search_keyboard(),
                            parse_mode="HTML")
    if sent:
        u["_search_msg_id"] = sent.message_id

    # ⭐ OPT #3: Fire instant matcher for this user
    asyncio.create_task(_try_instant_match(context, uid))


# ══════════════════════════════════════════════════════════════
# ⭐ OPTIMIZATION #2 + #4: Parallel pairs + bucketed background_matcher
# ══════════════════════════════════════════════════════════════
async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    """Background matcher — parallel pair connections + bucket lookup.

    ⚡ OPT #2: Multiple pairs connected in parallel (3 pairs: 2.1s → 0.7s)
    ⚡ OPT #4: O(1) bucket lookup instead of O(n²) scan
    """
    if state.maintenance_mode:
        return

    pairs = []

    async with queue_lock:
        if len(queue) < 2:
            return

        # Self-heal: sync buckets from queue_set (in case of desync from external removals)
        for qid in queue_set:
            qu = users.get(qid)
            if qu and qu.get("state") == "SEARCHING" and not qu.get("partner"):
                _add_to_bucket(qid, qu)

        waiting_uids = list(queue_set)
        # VIP priority
        waiting_uids.sort(key=lambda x: 0 if (users.get(x, {}).get("is_vip")) else 1)
        matched = set()

        for uid in waiting_uids:
            if uid in matched:
                continue
            u = users.get(uid)
            if not u or u.get("state") != "SEARCHING" or u.get("partner") or u.get("is_banned"):
                continue

            match_id = _find_match_from_buckets(uid, u, exclude=matched)
            if match_id is not None:
                matched.add(uid)
                matched.add(match_id)
                pairs.append((uid, match_id))

        # Remove all matched (from queue + bucket) under lock
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
            _remove_from_bucket_by_uid(a)
            _remove_from_bucket_by_uid(b)
            ua = users.get(a)
            ub = users.get(b)
            if ua:
                ua["state"] = "MATCHING"
            if ub:
                ub["state"] = "MATCHING"

    if not pairs:
        return

    # ⚡ OPT #2: Parallel pair connections
    async def _safe_connect(a: int, b: int):
        try:
            await connect_users(context, a, b)
        except Exception as e:
            logger.error(f"connect_users failed for ({a},{b}): {e}", exc_info=True)
            for uid in (a, b):
                ux = users.get(uid)
                if ux and ux.get("state") == "MATCHING":
                    ux["state"] = "IDLE"

    await asyncio.gather(
        *[_safe_connect(a, b) for a, b in pairs],
        return_exceptions=True,
    )

    if pairs:
        logger.info(f"⚡ Batch matched {len(pairs)} pairs in parallel")


# ══════════════════════════════════════════════════════════════
# REPORT — unchanged (instant admin DM + log channel)
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

    # INSTANT log + DM to owner + all admins
    try:
        from services.moderation import log_report
        await log_report(context, uid, partner_id, reason=None)
    except Exception as e:
        logger.error(f"log_report failed: {e}")

    # ALSO log to channel
    try:
        await log_event(
            context, "REPORT",
            category="user", level="warn",
            actor_id=uid,
            target_id=partner_id,
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


async def block_internal(context, uid: int):
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

    try:
        from state import active_voice_rooms
        for rid, r in list(active_voice_rooms.items()):
            if {r["u1"], r["u2"]} == {uid, partner_id}:
                active_voice_rooms.pop(rid, None)
    except Exception:
        pass

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
            actor_id=uid,
            target_id=partner_id,
            details={"Total blocked": total},
        )
    except Exception:
        pass


async def cancel_search(context, uid: int):
    u = await get_user(uid)
    if not u:
        return
    name = u.get("name") or "there"

    await _delete_searching_card(context, uid)

    async with queue_lock:
        try:
            queue.remove(uid)
        except ValueError:
            pass
        queue_set.discard(uid)
        _remove_from_bucket_by_uid(uid)   # ⭐ OPT #4
    if u.get("state") == "SEARCHING":
        u["state"] = "IDLE"

    body = _build_cancel_card(name, u)
    await safe_send(context, uid, body, parse_mode="HTML",
                    reply_markup=get_main_keyboard())


async def end_chat_internal(context, uid: int):
    u = await get_user(uid)
    name = esc(u.get("name") if u else "there")
    if not u or (not u.get("partner") and u.get("state") != "SEARCHING"):
        return await safe_send(
            context, uid,
            box_simple("Notice",
                       f"⚠️ Hey {name}, you are not in a chat or search.",
                       emoji="⚠️"),
            parse_mode="HTML", reply_markup=get_main_keyboard(),
        )
    if u.get("state") == "SEARCHING":
        await cancel_search(context, uid)
        return
    await disconnect(context, uid, u["partner"], ender_id=uid)