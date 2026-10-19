"""Anonymous VIP Save Partner — nickname flow + token consent + pagination."""
import logging
import secrets

from telegram import InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    MAX_SAVED_PARTNERS, BOT_USERNAME,
    SAVE_PARTNER_NOTIFY_PARTNER, SAVE_REQUEST_TOKEN_TTL,
)
from state import (
    users, analytics, save_request_tokens,
)
from database import get_user, save_user_to_db, users_collection
from utils import box_card, box_simple, safe_send, utcnow, esc, ikb

logger = logging.getLogger("sparktalks")

PAGE_SIZE = 5


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


async def start_save_partner(context: ContextTypes.DEFAULT_TYPE, uid: int,
                              partner_id: int = None) -> bool:
    """Step 1: Sender taps Save Partner → ask for nickname.
    partner_id: explicit target (safer than _last_partner_id)."""
    logger.info(f"💾 start_save_partner called for {uid} partner={partner_id}")

    u = await get_user(uid)
    if not u:
        return False

    if not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            ikb("🛍️ Get VIP", callback_data="BUY_STORE"),
        ]])
        return await safe_send(
            context, uid,
            box_card("VIP Only", [
                {"type": "text", "content": "⚠️ Save Partner is a VIP feature."},
                {"type": "divider"},
                {"type": "text", "content": "Upgrade to save & reconnect anonymously!"},
            ], emoji="👑"),
            reply_markup=kb, parse_mode="HTML",
        )

    target_id = partner_id or u.get("_last_partner_id")
    if not target_id:
        return await safe_send(
            context, uid,
            box_simple("No Partner", "⚠️ No recent partner to save.", emoji="⚠️"),
            parse_mode="HTML",
        )

    try:
        target_id = int(target_id)
    except (ValueError, TypeError):
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ Invalid partner.", emoji="⚠️"),
            parse_mode="HTML",
        )

    saved = u.setdefault("saved_partner_ids", [])
    if not isinstance(saved, list):
        saved = []
        u["saved_partner_ids"] = saved

    if target_id in saved:
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

    partner = users.get(target_id)
    if not partner:
        partner = await get_user(target_id)
    if not partner or partner.get("is_banned"):
        return await safe_send(
            context, uid,
            box_simple("Unavailable", "🚫 This partner is not available.", emoji="🚫"),
            parse_mode="HTML",
        )

    u["awaiting_input"] = "save_nick"
    u["_pending_save_partner_id"] = target_id
    await save_user_to_db(uid, u)

    kb = InlineKeyboardMarkup([[
        ikb("⏭️ Skip (auto-name)", callback_data="SAVE_NICK_SKIP"),
        ikb("❌ Cancel", callback_data="SAVE_NICK_CANCEL"),
    ]])

    sent = await safe_send(
        context, uid,
        box_card("Save Partner", [
            {"type": "text", "content": "💾 <b>Give a nickname to remember them</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "📝 <b>Type a short name</b> (max 20 chars)\n"
                "   e.g. <i>Movie buddy</i>, <i>Anime fan</i>"},
            {"type": "divider"},
            {"type": "text", "content": "👇 <b>Type below and send</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "🔒 <i>Nickname stays private. Partner never sees it.</i>"},
        ], emoji="💾"),
        reply_markup=kb, parse_mode="HTML",
    )

    return bool(sent)


async def finalize_save(context: ContextTypes.DEFAULT_TYPE,
                        uid: int, nickname: str = None) -> bool:
    """Save the partner + send consent request. NO FALLBACK to _last_partner_id."""
    logger.info(f"💾 finalize_save for {uid}, nickname={nickname!r}")

    u = await get_user(uid)
    if not u:
        return False

    partner_id = u.get("_pending_save_partner_id")
    # ⭐ FIX: Do NOT fall back to _last_partner_id — could save WRONG partner
    if not partner_id:
        u["awaiting_input"] = None
        return await safe_send(
            context, uid,
            box_simple("Session Expired",
                       "⚠️ Save session expired.\nTap 'Save Partner' again.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    try:
        partner_id = int(partner_id)
    except (ValueError, TypeError):
        u["awaiting_input"] = None
        u["_pending_save_partner_id"] = None
        return await safe_send(
            context, uid,
            box_simple("Error", "⚠️ Invalid partner.", emoji="⚠️"),
            parse_mode="HTML",
        )

    saved = u.setdefault("saved_partner_ids", [])
    nicks = u.setdefault("saved_partner_nicks", [])
    if not isinstance(saved, list):
        saved = []
        u["saved_partner_ids"] = saved
    if not isinstance(nicks, list):
        nicks = []
        u["saved_partner_nicks"] = nicks

    u["awaiting_input"] = None
    u["_pending_save_partner_id"] = None

    if partner_id in saved:
        await save_user_to_db(uid, u)
        return await safe_send(
            context, uid,
            box_simple("Already Saved", "💾 This partner is already saved.", emoji="💾"),
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

    while len(nicks) < len(saved):
        nicks.append("")

    auto_idx = len(saved) + 1
    nick = nickname.strip()[:20] if (nickname and nickname.strip()) else f"Partner #{auto_idx}"

    _cleanup_tokens()
    token = _new_token()
    save_request_tokens[token] = {
        "saver": uid,
        "receiver": partner_id,
        "nick": nick,
        "created": utcnow().timestamp(),
    }

    kb = InlineKeyboardMarkup([[
        ikb("✅ Allow", callback_data=f"SAVE_ACCEPT:{token}"),
        ikb("❌ Decline", callback_data=f"SAVE_DECLINE:{token}"),
    ]])

    sent = await safe_send(
        context, partner_id,
        box_card("Save Request", [
            {"type": "text", "content": "💾 <b>Someone wants to save you</b>"},
            {"type": "divider"},
            {"type": "text", "content":
                "🔒 Fully anonymous — no identity is revealed."},
            {"type": "divider"},
            {"type": "text", "content":
                "They may reconnect with you later.\n"
                "You can accept or decline."},
            {"type": "divider"},
            {"type": "text", "content": "⏰ Request expires in 5 minutes"},
        ], emoji="💾"),
        reply_markup=kb, parse_mode="HTML",
    )

    if not sent:
        save_request_tokens.pop(token, None)
        await save_user_to_db(uid, u)
        return await safe_send(
            context, uid,
            box_simple("Failed",
                       "⚠️ Could not reach partner.\nThey may have blocked the bot.",
                       emoji="⚠️"),
            parse_mode="HTML",
        )

    await save_user_to_db(uid, u)

    await safe_send(
        context, uid,
        box_card("Request Sent", [
            {"type": "text", "content": f"✅ Nickname: <b>{esc(nick)}</b>"},
            {"type": "divider"},
            {"type": "text", "content": "⏳ Waiting for partner to approve..."},
            {"type": "divider"},
            {"type": "text", "content": "🔒 Your identity is not revealed to them."},
        ], emoji="⏳"),
        parse_mode="HTML",
    )
    return True


async def receiver_allow(context: ContextTypes.DEFAULT_TYPE,
                          receiver_id: int, token: str) -> bool:
    _cleanup_tokens()
    entry = save_request_tokens.pop(token, None)
    if not entry:
        return False
    if entry["receiver"] != receiver_id:
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
            {"type": "text", "content": f"✅ <b>{esc(nick)}</b> saved to your list."},
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
    return True


async def receiver_decline(context: ContextTypes.DEFAULT_TYPE,
                            receiver_id: int, token: str) -> bool:
    _cleanup_tokens()
    entry = save_request_tokens.pop(token, None)
    if not entry:
        return False
    if entry["receiver"] != receiver_id:
        return False
    await safe_send(
        context, entry["saver"],
        box_simple("Declined", "❌ Partner declined the save request.", emoji="❌"),
        parse_mode="HTML",
    )
    return True


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


def _build_saved_page_kb(u: dict, page: int):
    """Build paginated keyboard. 5 per page."""
    saved = u.get("saved_partner_ids", []) or []
    total = len(saved)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    buttons = []

    # Reconnect row
    row = []
    for i in range(start, end):
        row.append(ikb(f"🔄 #{i+1}", callback_data=f"RECONNECT_IDX:{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Unsave row
    row = []
    for i in range(start, end):
        row.append(ikb(f"❌ #{i+1}", callback_data=f"UNSAVE_IDX:{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Pagination row
    if total_pages > 1:
        page_row = []
        if page > 0:
            page_row.append(ikb("◀️ Prev", callback_data=f"SAVED_PAGE:{page - 1}"))
        page_row.append(ikb(f"{page+1}/{total_pages}", callback_data="NOOP"))
        if page < total_pages - 1:
            page_row.append(ikb("Next ▶️", callback_data=f"SAVED_PAGE:{page + 1}"))
        buttons.append(page_row)

    buttons.append([ikb("🏠 Dashboard", callback_data="BACK_DASHBOARD")])
    return InlineKeyboardMarkup(buttons)


async def list_saved_partners(context: ContextTypes.DEFAULT_TYPE, uid: int,
                               page: int = 0, edit_query=None) -> bool:
    u = await get_user(uid)
    if not u:
        return False

    saved_ids = u.get("saved_partner_ids", []) or []
    nicks = u.get("saved_partner_nicks", []) or []

    while len(nicks) < len(saved_ids):
        nicks.append(f"Partner #{len(nicks)+1}")

    if not saved_ids:
        body = box_card("Saved Partners", [
            {"type": "text", "content": "📭 Your saved list is empty."},
            {"type": "divider"},
            {"type": "text", "content":
                "💡 After a chat ends, tap '💾 Save Partner' to add them."},
            {"type": "divider"},
            {"type": "text", "content":
                "🔒 Fully anonymous — no identity is ever shown."},
        ], emoji="💾")
        if edit_query:
            try:
                await edit_query.edit_message_text(body, parse_mode="HTML")
            except Exception:
                pass
        else:
            await safe_send(context, uid, body, parse_mode="HTML")
        return True

    total_pages = max(1, (len(saved_ids) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(saved_ids))

    lines = [
        "💾  ✨  <b>Saved Partners</b>  ✨  💾",
        "▎",
        f"▎ Total : <b>{len(saved_ids)}/{MAX_SAVED_PARTNERS}</b>",
    ]
    if total_pages > 1:
        lines.append(f"▎ Page : <b>{page+1}/{total_pages}</b>")
    lines.extend([
        "▎",
        "▎ 🔒 <i>Anonymous — identities are hidden.</i>",
        "▎",
    ])

    for i in range(start, end):
        pid = saved_ids[i]
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

    kb = _build_saved_page_kb(u, page)

    if edit_query:
        try:
            await edit_query.edit_message_text(
                "\n".join(lines), reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            await safe_send(context, uid, "\n".join(lines),
                            reply_markup=kb, parse_mode="HTML")
    else:
        await safe_send(context, uid, "\n".join(lines),
                        reply_markup=kb, parse_mode="HTML")
    return True