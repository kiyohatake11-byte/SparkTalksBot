"""Anonymous VIP Save Partner — nickname flow + token consent.

Privacy design:
- Partner's real name/username/user_id NEVER shown.
- User gives a custom nickname (e.g., "Movie buddy").
- Saved list shows nickname + index (no raw uid).
- Callback data uses indices: RECONNECT_IDX:0
- Save request uses random tokens: SAVE_ACCEPT:<token>
- Partner is NOT notified when saved (silent)
"""
import logging
import secrets

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    MAX_SAVED_PARTNERS, BOT_USERNAME,
    SAVE_PARTNER_NOTIFY_PARTNER, SAVE_REQUEST_TOKEN_TTL,
)
from state import (
    users, analytics, save_request_tokens,
)
from database import get_user, save_user_to_db, users_collection
from utils import box_card, box_simple, safe_send, utcnow, esc

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# TOKEN HELPERS
# ══════════════════════════════════════════════════════════════

def _new_token() -> str:
    return secrets.token_urlsafe(8)


def _cleanup_tokens():
    now_ts = utcnow().timestamp()
    for tk in list(save_request_tokens.keys()):
        entry = save_request_tokens.get(tk)
        if not entry:
            continue
        if now_ts - entry.get("created", 0) > SAVE_REQUEST_TOKEN_TTL:
            save_request_tokens.pop(tk, None)


# ══════════════════════════════════════════════════════════════
# START SAVE PARTNER FLOW (nickname prompt)
# ══════════════════════════════════════════════════════════════

async def start_save_partner(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """Step 1: Sender taps Save Partner → ask for nickname."""
    logger.info(f"💾 start_save_partner called for {uid}")

    u = await get_user(uid)
    if not u:
        logger.warning(f"start_save_partner: no user for {uid}")
        return False

    # ─── VIP only ───────────────────────────────────────
    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
        ]])
        return await safe_send(
            context, uid,
            box_card("VIP Only", [
                {"type": "text", "content": "⚠️ Save Partner is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content":
                    "Upgrade to save & reconnect anonymously!"},
            ], emoji="👑"),
            reply_markup=kb, parse_mode="HTML",
        )

    partner_id = u.get("_last_partner_id")
    logger.info(f"💾 {uid} last partner: {partner_id}")
    if not partner_id:
        return await safe_send(
            context, uid,
            box_simple("No Partner", "⚠️ No recent partner to save.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    # Ensure partner_id is int
    try:
        partner_id = int(partner_id)
    except (ValueError, TypeError):
        logger.warning(f"Invalid partner_id {partner_id} for {uid}")
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ Invalid partner. Try again.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    saved = u.setdefault("saved_partner_ids", [])
    if not isinstance(saved, list):
        saved = []
        u["saved_partner_ids"] = saved

    if partner_id in saved:
        return await safe_send(
            context, uid,
            box_simple("Already Saved",
                       "💾 This partner is already in your saved list.",
                       emoji="💾"),
            parse_mode="HTML",
        )

    if len(saved) >= MAX_SAVED_PARTNERS:
        return await safe_send(
            context, uid,
            box_card("List Full", [
                {"type": "text", "content":
                    f"📦 Saved list is full ({MAX_SAVED_PARTNERS} max)."},
                {"type": "divider"},
                {"type": "text", "content": "Use /saved to manage & remove."},
            ], emoji="📦"),
            parse_mode="HTML",
        )

    # ─── Partner existence check ───────────────────────
    partner = users.get(partner_id)
    if not partner:
        partner = await get_user(partner_id)
    if not partner or partner.get("is_banned"):
        return await safe_send(
            context, uid,
            box_simple("Unavailable",
                       "🚫 This partner is not available.",
                       emoji="🚫"),
            parse_mode="HTML",
        )

    # ─── ⭐ Set state + PERSIST to DB ──────────────────
    u["awaiting_input"] = "save_nick"
    u["_pending_save_partner_id"] = partner_id
    await save_user_to_db(uid, u)
    logger.info(f"💾 {uid} awaiting nickname for partner {partner_id}")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ Skip (auto-name)", callback_data="SAVE_NICK_SKIP"),
        InlineKeyboardButton("❌ Cancel", callback_data="SAVE_NICK_CANCEL"),
    ]])

    sent = await safe_send(
        context, uid,
        box_card("Save Partner", [
            {"type": "text", "content":
                "💾 <b>Give a nickname to remember them</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "📝 <b>Type a short name</b> (max 20 chars)\n"
                "   e.g. <i>Movie buddy</i>, <i>Anime fan</i>"},
            {"type": "divider"},
            {"type": "text", "content":
                "👇 <b>Type below and send</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "🔒 <i>Nickname stays private. Partner never sees it.</i>"},
        ], emoji="💾"),
        reply_markup=kb, parse_mode="HTML",
    )

    if not sent:
        logger.error(f"Failed to send nickname prompt to {uid}")
        return False

    return True


# ══════════════════════════════════════════════════════════════
# FINALIZE SAVE (after nickname received or skip)
# ══════════════════════════════════════════════════════════════

async def finalize_save(context: ContextTypes.DEFAULT_TYPE,
                        uid: int, nickname: str = None) -> bool:
    """Actually save the partner + send consent request to partner."""
    logger.info(f"💾 finalize_save for {uid}, nickname={nickname!r}")

    u = await get_user(uid)
    if not u:
        return False

    # ⭐ FALLBACK: if _pending_save_partner_id missing, use _last_partner_id
    partner_id = u.get("_pending_save_partner_id")
    if not partner_id:
        partner_id = u.get("_last_partner_id")
        logger.warning(
            f"💾 {uid}: _pending_save_partner_id missing — "
            f"fallback to _last_partner_id={partner_id}"
        )

    if not partner_id:
        logger.warning(f"💾 {uid}: no partner_id at all")
        u["awaiting_input"] = None
        return await safe_send(
            context, uid,
            box_simple("No Partner",
                       "⚠️ No recent partner found. Start a chat first.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    try:
        partner_id = int(partner_id)
    except (ValueError, TypeError):
        u["awaiting_input"] = None
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ Invalid partner. Try again.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    saved = u.setdefault("saved_partner_ids", [])
    nicks = u.setdefault("saved_partner_nicks", [])
    if not isinstance(saved, list):
        saved = []; u["saved_partner_ids"] = saved
    if not isinstance(nicks, list):
        nicks = []; u["saved_partner_nicks"] = nicks

    # Clear pending state
    u["awaiting_input"] = None
    u["_pending_save_partner_id"] = None

    if partner_id in saved:
        await save_user_to_db(uid, u)
        return await safe_send(
            context, uid,
            box_simple("Already Saved",
                       "💾 This partner is already saved.",
                       emoji="💾"),
            parse_mode="HTML",
        )

    if len(saved) >= MAX_SAVED_PARTNERS:
        await save_user_to_db(uid, u)
        return await safe_send(
            context, uid,
            box_simple("List Full",
                       f"📦 Your list is full ({MAX_SAVED_PARTNERS} max).",
                       emoji="📦"),
            parse_mode="HTML",
        )

    # ─── Align nicks ───────────────────────────────────
    while len(nicks) < len(saved):
        nicks.append("")

    auto_idx = len(saved) + 1
    if nickname and nickname.strip():
        nick = nickname.strip()[:20]
    else:
        nick = f"Partner #{auto_idx}"

    # ─── Create anonymous token ────────────────────────
    _cleanup_tokens()
    token = _new_token()
    save_request_tokens[token] = {
        "saver": uid,
        "receiver": partner_id,
        "nick": nick,
        "created": utcnow().timestamp(),
    }

    # ─── Send anonymous consent request to partner ─────
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Allow", callback_data=f"SAVE_ACCEPT:{token}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"SAVE_DECLINE:{token}"),
    ]])

    logger.info(f"💾 {uid} sending consent request to {partner_id} (nick='{nick}')")

    sent = await safe_send(
        context, partner_id,
        box_card("Save Request", [
            {"type": "text", "content":
                "💾 <b>Someone wants to save you</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "🔒 Fully anonymous — no identity is revealed."},
            {"type": "divider"},
            {"type": "text", "content":
                "They may reconnect with you later.\n"
                "You can accept or decline."},
            {"type": "divider"},
            {"type": "text", "content":
                "⏰ Request expires in 5 minutes"},
        ], emoji="💾"),
        reply_markup=kb, parse_mode="HTML",
    )

    if not sent:
        save_request_tokens.pop(token, None)
        await save_user_to_db(uid, u)
        logger.error(f"💾 Failed to send consent request to partner {partner_id}")
        return await safe_send(
            context, uid,
            box_simple("Failed",
                       "⚠️ Could not reach partner.\n"
                       "They may have blocked the bot.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    await save_user_to_db(uid, u)

    await safe_send(
        context, uid,
        box_card("Request Sent", [
            {"type": "text", "content":
                f"✅ Nickname: <b>{esc(nick)}</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "⏳ Waiting for partner to approve..."},
            {"type": "divider"},
            {"type": "text", "content":
                "🔒 Your identity is not revealed to them."},
        ], emoji="⏳"),
        parse_mode="HTML",
    )
    logger.info(f"💾 {uid} → {partner_id} consent request sent successfully")
    return True


# ══════════════════════════════════════════════════════════════
# CONSENT FLOW (receiver side)
# ══════════════════════════════════════════════════════════════

async def receiver_allow(context: ContextTypes.DEFAULT_TYPE,
                          receiver_id: int, token: str) -> bool:
    _cleanup_tokens()
    entry = save_request_tokens.pop(token, None)
    if not entry:
        logger.warning(f"receiver_allow: no token {token}")
        return False

    if entry["receiver"] != receiver_id:
        logger.warning(f"Token {token} receiver mismatch")
        return False

    sender_id = entry["saver"]
    nick = entry.get("nick") or ""

    sender = await get_user(sender_id)
    if not sender:
        return False

    saved = sender.setdefault("saved_partner_ids", [])
    nicks = sender.setdefault("saved_partner_nicks", [])

    if receiver_id in saved:
        return True

    if len(saved) >= MAX_SAVED_PARTNERS:
        await safe_send(
            context, sender_id,
            box_simple("List Full",
                       f"📦 Your list is full ({MAX_SAVED_PARTNERS} max).",
                       emoji="📦"),
            parse_mode="HTML",
        )
        return False

    while len(nicks) < len(saved):
        nicks.append("")

    auto_idx = len(saved) + 1
    if not nick:
        nick = f"Partner #{auto_idx}"

    saved.append(receiver_id)
    nicks.append(nick)
    await save_user_to_db(sender_id, sender)
    analytics["saves_today"] += 1

    await safe_send(
        context, sender_id,
        box_card("Saved", [
            {"type": "text", "content":
                f"✅ <b>{esc(nick)}</b> saved to your list."},
            {"type": "divider"},
            {"type": "kv", "items": [
                ("💾 Total saved", f"<b>{len(saved)}/{MAX_SAVED_PARTNERS}</b>"),
            ]},
            {"type": "divider"},
            {"type": "text", "content": "🔒 View with /saved"},
        ], emoji="💾"),
        parse_mode="HTML",
    )

    if SAVE_PARTNER_NOTIFY_PARTNER:
        await safe_send(
            context, receiver_id,
            box_simple("Saved 💾",
                       "💾 Someone added you to their saved list.\n"
                       "🔒 Their identity remains anonymous.",
                       emoji="💾"),
            parse_mode="HTML",
        )

    logger.info(f"💾 {sender_id} saved {receiver_id} as '{nick}'")
    return True


async def receiver_decline(context: ContextTypes.DEFAULT_TYPE,
                            receiver_id: int, token: str) -> bool:
    _cleanup_tokens()
    entry = save_request_tokens.pop(token, None)
    if not entry:
        return False
    if entry["receiver"] != receiver_id:
        return False
    sender_id = entry["saver"]
    await safe_send(
        context, sender_id,
        box_simple("Declined",
                   "❌ Partner declined the save request.",
                   emoji="❌"),
        parse_mode="HTML",
    )
    return True


# ══════════════════════════════════════════════════════════════
# UNSAVE (index-based)
# ══════════════════════════════════════════════════════════════

async def unsave_partner_by_idx(context: ContextTypes.DEFAULT_TYPE,
                                 uid: int, idx: int) -> bool:
    u = await get_user(uid)
    if not u:
        return False
    saved = u.get("saved_partner_ids", []) or []
    nicks = u.get("saved_partner_nicks", []) or []
    if idx < 0 or idx >= len(saved):
        return False
    saved.pop(idx)
    if idx < len(nicks):
        nicks.pop(idx)
    u["saved_partner_ids"] = saved
    u["saved_partner_nicks"] = nicks
    await save_user_to_db(uid, u)
    return True


# ══════════════════════════════════════════════════════════════
# LIST (nickname-based, fully anonymous)
# ══════════════════════════════════════════════════════════════

async def list_saved_partners(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    u = await get_user(uid)
    if not u:
        return False

    saved_ids = u.get("saved_partner_ids", []) or []
    nicks = u.get("saved_partner_nicks", []) or []

    while len(nicks) < len(saved_ids):
        nicks.append(f"Partner #{len(nicks)+1}")

    if not saved_ids:
        return await safe_send(
            context, uid,
            box_card("Saved Partners", [
                {"type": "text", "content": "📭 Your saved list is empty."},
                {"type": "divider"},
                {"type": "text", "content":
                    "💡 After a chat ends, tap '💾 Save Partner' to add them."},
                {"type": "divider"},
                {"type": "text", "content":
                    "🔒 Fully anonymous — no identity is ever shown."},
            ], emoji="💾"),
            parse_mode="HTML",
        )

    lines = [
        "💾  ✨  <b>Saved Partners</b>  ✨  💾",
        "▎",
        f"▎ Total : <b>{len(saved_ids)}/{MAX_SAVED_PARTNERS}</b>",
        "▎",
        "▎ 🔒 <i>Anonymous — identities are hidden.</i>",
        "▎",
    ]

    for i, pid in enumerate(saved_ids):
        p = users.get(pid)
        if p and p.get("is_banned"):
            status = "🚫"
        elif p and p.get("partner"):
            status = "🔴"
        elif p and p.get("state") == "IDLE":
            status = "🟢"
        elif pid in users:
            status = "🟡"
        else:
            status = "⚪"

        nick = esc(nicks[i]) if i < len(nicks) else f"Partner #{i+1}"
        lines.append(f"▎ {i+1}. {status} <b>{nick}</b>")

    lines.append("▎")
    lines.append("▎ 💡 Tap a number below to reconnect or remove")

    buttons = []
    row = []
    for i in range(min(5, len(saved_ids))):
        row.append(InlineKeyboardButton(f"🔄 #{i+1}",
                                        callback_data=f"RECONNECT_IDX:{i}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)

    row = []
    for i in range(min(5, len(saved_ids))):
        row.append(InlineKeyboardButton(f"❌ #{i+1}",
                                        callback_data=f"UNSAVE_IDX:{i}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🏠 Dashboard",
                                          callback_data="BACK_DASHBOARD")])

    await safe_send(
        context, uid, "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )
    return True