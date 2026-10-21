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
from state import users, queue, queue_set, queue_lock, last_next_time, admin_cache, analytics
import state
from database import get_user, save_user_to_db, is_owner_or_admin
from utils import box_card, box_simple, safe_send, utcnow, to_bold, esc, ikb
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
    "💡 Tired of 2s wait? VIP = instant next! 🚀",
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
# DISCONNECT — with immediate DB persistence
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
        # ⭐ Direct DB write with retry
        for attempt in range(3):
            try:
                ok = await _do_save(uid, u)
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

        body = (
            f"🛑  ✨  <b>Chat Ended</b>  ✨  🛑\n"
            f"▎\n"
            f"▎ 💔 <b>{reason}</b>\n"
            f"▎\n"
        )
        if u and u.get("is_vip"):
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

        kb_markup = _build_end_keyboard(u, partner_id) if u else None
        await safe_send(context, uid, body, parse_mode="HTML", reply_markup=kb_markup)

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
            sent = await safe_send(context, u1, body,
                                    reply_markup=_cancel_search_keyboard(),
                                    parse_mode="HTML")
            if sent:
                u["_search_msg_id"] = sent.message_id


# ══════════════════════════════════════════════════════════════
# CONNECT_USERS — Direct DB write with retry
# ══════════════════════════════════════════════════════════════
async def connect_users(context, uid1: int, uid2: int):
    u1 = users.get(uid1)
    u2 = users.get(uid2)
    if not u1 or not u2:
        return

    from database import _do_save

    await _delete_searching_card(context, uid1)
    await _delete_searching_card(context, uid2)

    # ── Step 1: Update BOTH users in memory ──
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

    # ── Step 2: Persist BOTH users to DB (direct, with retry) ──
    async def _persist_with_retry(uid: int, partner_id: int, max_retries: int = 5) -> bool:
        u = users.get(uid)
        if not u:
            return False
        for attempt in range(max_retries):
            try:
                ok = await _do_save(uid, u)
                if ok:
                    logger.info(f"💾 Persisted chat {uid} ↔ {partner_id} (attempt {attempt+1})")
                    return True
                logger.warning(f"⚠️ _do_save returned False for {uid} (attempt {attempt+1})")
            except Exception as e:
                logger.error(f"❌ Persist chat {uid} attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.5 * (attempt + 1))
        logger.critical(
            f"🚨 CRITICAL: Failed to persist {uid} ↔ {partner_id} "
            f"after {max_retries} attempts!"
        )
        return False

    ok1 = await _persist_with_retry(uid1, uid2)
    ok2 = await _persist_with_retry(uid2, uid1)

    if not ok1 or not ok2:
        logger.error(
            f"⚠️ Chat {uid1}↔{uid2} may NOT survive restart! "
            f"(u1_ok={ok1}, u2_ok={ok2})"
        )

    # ── Step 3: Post-connect logic (credits, achievements) ──
    for uid, partner_id in ((uid1, uid2), (uid2, uid1)):
        u = users.get(uid)
        if not u:
            continue

        if not u.get("is_vip"):
            if u.get("pending_pref_from_credit") and u.get("gender_pref_credits", 0) > 0:
                u["gender_pref_credits"] = max(0, u["gender_pref_credits"] - 1)
                remaining = u["gender_pref_credits"]
                try:
                    await safe_send(
                        context, uid,
                        f"🎯  ✨  <b>Gender Filter Used</b>  ✨  🎯\n"
                        f"▎\n"
                        f"▎ ✅ 1 credit consumed\n"
                        f"▎ 🎁 Remaining credits : <b>{remaining}</b>\n"
                        f"▎\n"
                        f"💡 <i>Enjoy your match!</i>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            u["pref_gender"] = "Any"
            u["pending_pref_from_credit"] = False

        started = last_next_time.get(uid)
        if started:
            waited = utcnow().timestamp() - started
            if 0 < waited < 600:
                analytics["match_wait_total"] += waited
                analytics["match_wait_count"] += 1

        try:
            from services.achievements import unlock_achievement
            if u["total_matches"] == 1:
                await unlock_achievement(context, uid, "first_match")
            if u["total_matches"] == 10:
                await unlock_achievement(context, uid, "ten_matches")
            if u["total_matches"] == 50:
                await unlock_achievement(context, uid, "fifty_matches")
            if u["total_chats"] == 100:
                await unlock_achievement(context, uid, "hundred_chats")
        except Exception:
            pass

    analytics["matches_today"] += 1

    # ── Step 4: Send connection cards ──
    common = list(set(u1.get("interests", [])) & set(u2.get("interests", [])))

    await safe_send(context, uid1, _build_connect_card(u1, u2, common),
                    reply_markup=get_chat_keyboard(u1),
                    parse_mode="HTML", disable_web_page_preview=True)
    await safe_send(context, uid2, _build_connect_card(u2, u1, common),
                    reply_markup=get_chat_keyboard(u2),
                    parse_mode="HTML", disable_web_page_preview=True)


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
        await _delete_searching_card(context, uid)
        waiting = len(queue)
        body = _build_already_searching_card(name, waiting)
        sent = await safe_send(context, uid, body,
                                reply_markup=_cancel_search_keyboard(),
                                parse_mode="HTML")
        if sent:
            u["_search_msg_id"] = sent.message_id
        return

    # Free-user gender preference handling
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
            if found in users:
                users[found]["state"] = "MATCHING"
            u["state"] = "MATCHING"

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
    sent = await safe_send(context, uid, body,
                            reply_markup=_cancel_search_keyboard(),
                            parse_mode="HTML")
    if sent:
        u["_search_msg_id"] = sent.message_id

    try:
        task = asyncio.create_task(background_matcher(context))
        task.add_done_callback(
            lambda t: t.exception() and logger.error(
                f"Instant matcher failed: {t.exception()}"
            )
        )
    except Exception as e:
        logger.debug(f"Could not schedule instant matcher: {e}")


async def background_matcher(context: ContextTypes.DEFAULT_TYPE):
    if state.maintenance_mode:
        return

    pairs = []

    async with queue_lock:
        if len(queue) < 2:
            return
        waiting = list(queue)
        waiting.sort(key=lambda x: 0 if (users.get(x, {}).get("is_vip")) else 1)
        matched = set()

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
            ua = users.get(a)
            ub = users.get(b)
            if ua:
                ua["state"] = "MATCHING"
            if ub:
                ub["state"] = "MATCHING"

    for a, b in pairs:
        try:
            await connect_users(context, a, b)
        except Exception as e:
            logger.error(f"connect_users failed for ({a},{b}): {e}", exc_info=True)
            for uid in (a, b):
                u = users.get(uid)
                if u and u.get("state") == "MATCHING":
                    u["state"] = "IDLE"


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

    analytics["reports_today"] += 1

    admin_body = box_card("Report Received", [
        {"type": "kv", "items": [
            ("👤 Reporter", f"<code>{uid}</code>"),
            ("🎯 Reported", f"<code>{partner_id}</code>"),
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