"""
Anonymous WebRTC Voice Rooms.

Flow:
1. VIP user in chat sends /voice (or taps 🎙️ Voice Call button)
2. Bot creates a temporary room_id + secret token
3. Both partners receive a web link: /voice/<room_id>?token=...
4. Browser opens WebRTC peer-to-peer voice (no Telegram identity)
5. Room auto-expires after VOICE_ROOM_EXPIRE_SECONDS

Only VIP members can INITIATE a voice room.
The partner (even non-VIP) can JOIN if invited.
"""
import secrets
import logging
from typing import Optional

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    VOICE_ROOM_EXPIRE_SECONDS,
    VOICE_ROOM_BASE_URL,
    VOICE_ROOM_MIN_VIP,
    PORT,
)
from state import users, active_voice_rooms, analytics
from utils import safe_send, utcnow

logger = logging.getLogger("sparktalks")


def _generate_room_id() -> str:
    return secrets.token_urlsafe(12)


def _generate_token() -> str:
    return secrets.token_urlsafe(16)


def _get_base_url() -> str:
    """Return the public base URL for voice room links."""
    if VOICE_ROOM_BASE_URL:
        return VOICE_ROOM_BASE_URL.rstrip("/")
    import os
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        return render_url.rstrip("/")
    return f"http://localhost:{PORT}"


async def create_voice_room(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    """Create an anonymous WebRTC voice room for the user and their partner.

    VIP-ONLY to initiate. The partner (even if non-VIP) can join once invited.
    """
    u = users.get(uid)
    if not u or not u.get("partner"):
        await safe_send(context, uid, "⚠️ Not in an active chat.", parse_mode="HTML")
        return False

    # 🎙️ VIP-ONLY to INITIATE — partner (even non-VIP) can join once invited
    if VOICE_ROOM_MIN_VIP and not u.get("is_vip"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
        ]])
        body = (
            "🎙️  ✨  <b>Voice Call — VIP Feature</b>  ✨  🎙️\n"
            "▎\n"
            "▎ ⚠️ Only <b>VIP members</b> can start a voice call.\n"
            "▎\n"
            "▎ 👑  <b>VIP Perks</b>\n"
            "▎   ├ 🎙️ Anonymous voice calls\n"
            "▎   ├ 🚻 Gender filter\n"
            "▎   ├ 🚫 Block users\n"
            "▎   ├ ⚡ Priority matching\n"
            "▎   └ 🔄 Unlimited /next\n"
            "▎\n"
            "▎ 💡 <i>Upgrade to VIP to unlock voice calls!</i>"
        )
        return await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")

    pid = u["partner"]

    # Reuse existing active room for the same pair
    for room in active_voice_rooms.values():
        if {room["u1"], room["u2"]} == {uid, pid}:
            if utcnow().timestamp() - room["created"] < VOICE_ROOM_EXPIRE_SECONDS:
                return await _send_links_both(context, uid, pid, room, existing=True)

    room_id = _generate_room_id()
    token_a = _generate_token()
    token_b = _generate_token()

    room = {
        "u1": uid,
        "u2": pid,
        "token_u1": token_a,
        "token_u2": token_b,
        "created": utcnow().timestamp(),
        "participants": set(),
    }
    active_voice_rooms[room_id] = room
    analytics["voice_rooms_today"] += 1

    logger.info(f"Created anonymous voice room {room_id} for {uid} ↔ {pid}")
    return await _send_links_both(context, uid, pid, room, room_id=room_id, existing=False)


async def _send_links_both(
    context,
    uid: int,
    pid: int,
    room: dict,
    room_id: str = None,
    existing: bool = False,
):
    if room_id is None:
        for rid, r in active_voice_rooms.items():
            if r is room:
                room_id = rid
                break
    if not room_id:
        return False

    base = _get_base_url()
    link_u1 = f"{base}/voice/{room_id}?token={room['token_u1']}"
    link_u2 = f"{base}/voice/{room_id}?token={room['token_u2']}"

    expire_min = VOICE_ROOM_EXPIRE_SECONDS // 60
    prefix = "🔄 <b>Voice Room Reopened</b>" if existing else "🎙️ <b>Anonymous Voice Room Ready!</b>"

    body_template = (
        f"{prefix}\n"
        "▎\n"
        "▎ 🔒 <b>Fully Anonymous</b> — no names or profiles shown\n"
        "▎\n"
        "▎ 👥 <b>How to join</b>\n"
        "▎   ├ 1️⃣ Tap the button below\n"
        "▎   ├ 2️⃣ Allow microphone access\n"
        "▎   └ 3️⃣ Start talking!\n"
        "▎\n"
        f"▎ ⏱ Room expires in <b>{expire_min} min</b>\n"
        "▎ 🌐 Works best on Chrome / Safari"
    )

    kb1 = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎙️ Join Anonymous Voice Room", url=link_u1)
    ]])
    kb2 = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎙️ Join Anonymous Voice Room", url=link_u2)
    ]])

    await safe_send(context, uid, body_template, reply_markup=kb1, parse_mode="HTML")
    await safe_send(context, pid, body_template, reply_markup=kb2, parse_mode="HTML")
    return True


def get_room(room_id: str) -> Optional[dict]:
    return active_voice_rooms.get(room_id)


def validate_token(room_id: str, token: str) -> Optional[int]:
    """Return the user_id if token is valid for this room, else None."""
    room = active_voice_rooms.get(room_id)
    if not room:
        return None
    if room["token_u1"] == token:
        return room["u1"]
    if room["token_u2"] == token:
        return room["u2"]
    return None


def is_room_expired(room: dict) -> bool:
    return (utcnow().timestamp() - room["created"]) > VOICE_ROOM_EXPIRE_SECONDS


async def cleanup_voice_rooms(context=None):
    """Called periodically to remove expired rooms from memory."""
    now = utcnow().timestamp()
    expired = [
        rid for rid, r in active_voice_rooms.items()
        if now - r["created"] > VOICE_ROOM_EXPIRE_SECONDS
    ]
    for rid in expired:
        active_voice_rooms.pop(rid, None)
    if expired:
        logger.info(f"Cleaned {len(expired)} expired voice rooms")