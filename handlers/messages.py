import logging
import asyncio
import hashlib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    BTN_FIND, BTN_SETTINGS, BTN_VIP, BTN_PROFILE,
    BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK,
    BTN_VOICE, BTN_VOICE_LOCKED,
    MAX_PENDING_MEDIA, MAX_REACTION_ENTRIES,
    ANTI_LINK_ENABLED, SYNC_MESSAGE_EDITS, FLOOD_MUTE_SECONDS,
    DUPLICATE_TEXT_THRESHOLD, DUPLICATE_TEXT_WINDOW,
    MEDIA_TIMEOUT_SECONDS,
)
from state import (
    users, message_reactions_map, message_edit_map, muted_users, analytics,
    duplicate_text_map, pending_media_timeouts,
)
from database import get_user, save_user_to_db
from utils import box_card, safe_send, split_message, to_bold, utcnow
from keyboards import get_main_keyboard, get_settings_text, get_settings_main_kb
from services.moderation import (
    contains_contact_info, handle_violation, is_muted,
    is_flooding, mute_remaining_seconds, is_contact_share,
)
from services.image_hash import check_image_duplicate, get_message_file_bytes
from handlers.commands import (
    cmd_next, cmd_settings, cmd_buy, cmd_profile,
    cmd_end, cmd_report, cmd_block, cmd_voice,
)

logger = logging.getLogger("sparktalks")

TYPING_BASE_DELAY = 0.05
TYPING_CHAR_RATE = 0.003
TYPING_MAX_DELAY = 0.4
TYPING_MIN_LENGTH = 15


def _calc_typing_delay(text: str) -> float:
    d = TYPING_BASE_DELAY + (len(text.strip()) * TYPING_CHAR_RATE)
    return min(d, TYPING_MAX_DELAY)


def _remember_mapping(uid: int, msg_id: int, pid: int, pmsg_id: int):
    message_reactions_map[f"{pid}:{pmsg_id}"] = {"target_chat": uid, "target_msg": msg_id}
    message_reactions_map[f"{uid}:{msg_id}"] = {"target_chat": pid, "target_msg": pmsg_id}
    message_edit_map[f"{uid}:{msg_id}"] = {"target_chat": pid, "target_msg": pmsg_id}
    message_edit_map[f"{pid}:{pmsg_id}"] = {"target_chat": uid, "target_msg": msg_id}
    while len(message_reactions_map) > MAX_REACTION_ENTRIES:
        message_reactions_map.popitem(last=False)
    while len(message_edit_map) > MAX_REACTION_ENTRIES:
        message_edit_map.popitem(last=False)


def _is_duplicate_text(uid: int, text: str) -> bool:
    if not text or len(text.strip()) < 5:
        return False
    now_ts = utcnow().timestamp()
    text_hash = hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]

    user_map = duplicate_text_map.setdefault(uid, {})
    timestamps = user_map.get(text_hash, [])
    timestamps = [t for t in timestamps if now_ts - t < DUPLICATE_TEXT_WINDOW]
    timestamps.append(now_ts)
    user_map[text_hash] = timestamps

    return len(timestamps) >= DUPLICATE_TEXT_THRESHOLD


async def relay_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    # ⭐ Guard: channel posts / anonymous admins have from_user=None
    if not msg.from_user:
        return
    uid = msg.from_user.id
    u = await get_user(uid)
    if u and u.get("is_banned"):
        return

    # Reply-keyboard buttons
    if msg.text:
        text = msg.text.strip()
        if text == BTN_FIND:      return await cmd_next(update, context)
        if text == BTN_SETTINGS:  return await cmd_settings(update, context)
        if text == BTN_VIP:       return await cmd_buy(update, context)
        if text == BTN_PROFILE:   return await cmd_profile(update, context)
        if text == BTN_NEXT:      return await cmd_next(update, context)
        if text == BTN_END:       return await cmd_end(update, context)
        if text == BTN_REPORT:    return await cmd_report(update, context)
        if text == BTN_BLOCK:     return await cmd_block(update, context)
        if text in (BTN_VOICE, BTN_VOICE_LOCKED):
            return await cmd_voice(update, context)

    # Bio input
    if u and u.get("awaiting_input") == "bio":
        if msg.text:
            u["bio"] = msg.text[:120]
            u["awaiting_input"] = None
            await save_user_to_db(uid, u)
            await msg.reply_text(
                get_settings_text(u),
                reply_markup=get_settings_main_kb(u),
                parse_mode="HTML",
            )
        else:
            await msg.reply_text("⚠️ Please send text for bio.")
        return

    # Save Partner Nickname input
    if u and u.get("awaiting_input") == "save_nick":
        logger.info(f"💾 Nickname input from {uid}: {msg.text!r}")
        if msg.text:
            nick = msg.text.strip()[:20]
            if not nick:
                await msg.reply_text(
                    "⚠️ Please send a short nickname (1-20 chars), or tap Skip."
                )
                return
            from services.saved_partners import finalize_save
            ok = await finalize_save(context, uid, nickname=nick)
            if not ok:
                await msg.reply_text(
                    "⚠️ Save failed. Please try again from /next → Save Partner."
                )
        else:
            await msg.reply_text(
                "⚠️ Please send text as nickname, or tap Skip."
            )
        return

    # Mute check
    if is_muted(uid):
        remaining = mute_remaining_seconds(uid)
        return await msg.reply_text(f"🔇 You are muted. Try again in {remaining}s.")

    # Not in chat
    if not u or not u.get("partner"):
        name = u.get("name") if u else "there"
        body = (
            f"💡  ✨  <b>Not Connected</b>  ✨  💡\n"
            f"▎\n"
            f"▎ 👋 Hey <b>{name}</b>, you're not in a chat.\n"
            f"▎\n"
            f"▎ 🎯 <b>Get Started</b>\n"
            f"▎   ├ 🎲 /next — Find a partner\n"
            f"▎   ├ ⚙️ /settings — Preferences\n"
            f"▎   └ 🛍️ /buy — Unlock VIP"
        )
        return await msg.reply_text(body, parse_mode="HTML", reply_markup=get_main_keyboard())

    # Flood
    if is_flooding(uid):
        muted_users[uid] = utcnow().timestamp() + FLOOD_MUTE_SECONDS
        analytics["mutes_today"] += 1
        try:
            from services.matching import disconnect
            await disconnect(context, uid, u["partner"], ender_id=uid)
        except Exception:
            pass
        return await msg.reply_text(
            f"🚫 Slow down! Muted for {FLOOD_MUTE_SECONDS // 60} min."
        )

    # Duplicate text spam
    if msg.text and _is_duplicate_text(uid, msg.text):
        muted_users[uid] = utcnow().timestamp() + FLOOD_MUTE_SECONDS
        analytics["mutes_today"] += 1
        try:
            from services.matching import disconnect
            await disconnect(context, uid, u["partner"], ender_id=uid)
        except Exception:
            pass
        return await msg.reply_text(
            "🔁 Duplicate messages detected. Muted for spamming."
        )

    # Anti-link
    if ANTI_LINK_ENABLED and msg.text:
        is_v, reason = contains_contact_info(msg.text)
        if is_v:
            analytics["violations_today"] += 1
            await handle_violation(context, uid, u, reason, msg.text)
            return

    # Contact card
    if is_contact_share(msg):
        analytics["violations_today"] += 1
        await handle_violation(context, uid, u, "contact_share", "[contact]")
        return

    # Image hash spam
    if msg.photo or (msg.document and (msg.document.mime_type or "").startswith("image/")) \
            or msg.animation:
        try:
            file_bytes = await get_message_file_bytes(context, msg)
            if file_bytes:
                blocked = await check_image_duplicate(context, uid, u, file_bytes)
                if blocked:
                    return
        except Exception as e:
            logger.debug(f"Image hash check skipped: {e}")

    pid = u["partner"]
    partner = users.get(pid)

    # Typing indicator
    if msg.text and len(msg.text.strip()) >= TYPING_MIN_LENGTH:
        delay = _calc_typing_delay(msg.text)
        try:
            await context.bot.send_chat_action(chat_id=pid, action="typing")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"Typing failed: {e}")

    # TEXT
    if msg.text:
        parts = split_message(msg.text)
        first_sent = None
        for part in parts:
            sent = await safe_send(context, pid, part)
            if sent and first_sent is None:
                first_sent = sent
        if first_sent:
            _remember_mapping(uid, msg.message_id, pid, first_sent.message_id)

    # MEDIA
    elif _is_media(msg):
        try:
            action = _media_action(msg)
            await context.bot.send_chat_action(chat_id=pid, action=action)
            await asyncio.sleep(0.1)
        except Exception:
            pass

        if partner and partner.get("confirm_media", True):
            pm_map = partner.get("pending_media") or {}
            pm_map[msg.message_id] = {"from_id": uid, "msg_id": msg.message_id}
            if len(pm_map) > MAX_PENDING_MEDIA:
                oldest = next(iter(pm_map))
                pm_map.pop(oldest, None)
            partner["pending_media"] = pm_map

            media_type = _media_type_label(msg)
            prompt = (
                f"📷  ✨  <b>Media Request</b>  ✨  📷\n"
                f"▎\n"
                f"▎ 🔔 Partner wants to send:\n"
                f"▎\n"
                f"▎ 📎 Type : <b>{to_bold(media_type)}</b>\n"
                f"▎ 🛡️ Shield : <b>ON</b>\n"
                f"▎ ⏰ Auto-decline in <b>{MEDIA_TIMEOUT_SECONDS // 60} min</b>"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("👁️ Accept", callback_data=f"MEDIA_ACCEPT:{msg.message_id}"),
                InlineKeyboardButton("🚫 Decline", callback_data=f"MEDIA_DECLINE:{msg.message_id}"),
            ]])
            sent_prompt = await safe_send(context, pid, prompt, reply_markup=kb, parse_mode="HTML")

            if sent_prompt:
                pending_media_timeouts[(uid, msg.message_id)] = {
                    "partner_id": pid,
                    "prompt_msg_id": sent_prompt.message_id,
                    "created": utcnow().timestamp(),
                }

            await msg.reply_text(
                f"⏳ Waiting for partner approval ({MEDIA_TIMEOUT_SECONDS // 60} min)..."
            )
        else:
            try:
                sent = await context.bot.copy_message(
                    chat_id=pid, from_chat_id=uid, message_id=msg.message_id
                )
                if sent:
                    _remember_mapping(uid, msg.message_id, pid, sent.message_id)
            except Exception as e:
                logger.error(f"Media relay error: {e}")


def _is_media(msg) -> bool:
    return bool(
        msg.photo or msg.video or msg.voice or msg.sticker
        or msg.document or msg.animation or msg.video_note or msg.audio
    )


def _media_action(msg) -> str:
    if msg.photo: return "upload_photo"
    if msg.video: return "upload_video"
    if msg.video_note: return "upload_video_note"
    if msg.voice: return "record_voice"
    if msg.audio: return "upload_voice"
    if msg.animation: return "upload_photo"
    if msg.sticker: return "choose_sticker"
    return "upload_document"


def _media_type_label(msg) -> str:
    if msg.photo: return "Photo"
    if msg.video: return "Video"
    if msg.video_note: return "Video Note"
    if msg.voice: return "Voice"
    if msg.audio: return "Audio"
    if msg.animation: return "GIF"
    if msg.sticker: return "Sticker"
    if msg.document: return "Document"
    return "Media"


async def on_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SYNC_MESSAGE_EDITS:
        return
    msg = update.edited_message
    if not msg or not msg.text:
        return
    if not msg.from_user:
        return
    uid = msg.from_user.id
    key = f"{uid}:{msg.message_id}"
    mapping = message_edit_map.get(key)
    if not mapping:
        return
    u = await get_user(uid)
    if not u or not u.get("partner"):
        return
    if ANTI_LINK_ENABLED:
        is_v, reason = contains_contact_info(msg.text)
        if is_v:
            try:
                await context.bot.delete_message(chat_id=uid, message_id=msg.message_id)
            except Exception:
                pass
            await handle_violation(context, uid, u, reason, msg.text)
            return
    try:
        await context.bot.edit_message_text(
            chat_id=mapping["target_chat"],
            message_id=mapping["target_msg"],
            text=msg.text,
        )
    except Exception as e:
        logger.debug(f"Edit sync failed: {e}")


async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not reaction:
        return
    key = f"{reaction.chat.id}:{reaction.message_id}"
    mapping = message_reactions_map.get(key)
    if mapping:
        try:
            await context.bot.set_message_reaction(
                chat_id=mapping["target_chat"],
                message_id=mapping["target_msg"],
                reaction=reaction.new_reaction,
            )
        except Exception as e:
            logger.error(f"Reaction sync error: {e}")