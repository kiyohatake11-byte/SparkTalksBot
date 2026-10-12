import logging
import random
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    AVAILABLE_INTERESTS, TIMEZONE_OPTIONS, SUPPORTED_LANGUAGES,
    REFERRAL_REWARD_DAYS, REFERRAL_REWARDS_NEEDED,
    BOT_USERNAME,
)
from state import users, active_voice_rooms, analytics
from database import get_user, save_user_to_db, is_owner_or_admin, users_collection
from utils import box_card, box_simple, safe_send, to_bold, utcnow
from keyboards import (
    get_main_keyboard, get_store_markup, get_settings_text,
    get_settings_main_kb, get_gender_pref_kb, get_age_kb,
    get_country_kb, get_interests_kb, get_language_kb,
    get_timezone_kb, get_theme_kb,
)
from services.matching import (
    try_match, end_chat_internal, report_internal, block_internal,
    cancel_search,
)
from services.vip import send_vip_invoice
from services.broadcast import execute_broadcast
from services.voice_rooms import create_voice_room

logger = logging.getLogger("sparktalks")

FORCE_DELETE_TIMEOUT = 15.0


# ══════════════════════════════════════════════════════════════
# SAFE EDIT HELPERS — work on both text messages AND photo captions
# ══════════════════════════════════════════════════════════════

async def _safe_edit(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Edit a message safely — works whether the original was text or a photo.
    Fallback: delete & send new message.
    """
    # Try 1: edit as text
    try:
        return await query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode
        )
    except Exception as e:
        err = str(e).lower()
        # Try 2: edit caption (if it was a photo)
        if "no text" in err or "caption" in err or "media" in err:
            try:
                return await query.edit_message_caption(
                    caption=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
            except Exception:
                pass
        # Try 3: delete + send new
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            return await query.message.reply_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except Exception as e2:
            logger.error(f"_safe_edit fallback failed: {e2}")
            return None


def _sidebar_styled(title: str, emoji: str, lines: list, tip: str = None) -> str:
    parts = [f"{emoji}  ✨  <b>{title}</b>  ✨  {emoji}", "▎"]
    for l in lines:
        parts.append(f"▎ {l}" if l else "▎")
    if tip:
        parts.append("▎")
        parts.append(f"💡 <i>{tip}</i>")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# MAIN CALLBACK ROUTER
# ══════════════════════════════════════════════════════════════

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    u = await get_user(uid)
    if not u:
        await query.answer("Please run /start first.", show_alert=True)
        return
    if u.get("is_banned"):
        await query.answer("You are banned.", show_alert=True)
        return

    data = query.data
    name = u.get("name") or "there"

    # ═══ GENDER SETUP ═══
    if data in ("G_MALE", "G_FEMALE"):
        if u.get("gender"):
            await query.answer("⚠️ Gender already set.", show_alert=True)
            return
        await query.answer()
        u["temp"] = "Male" if data == "G_MALE" else "Female"
        body = box_card("Almost there", [
            {"type": "text", "content": f"You selected: <b>{u['temp']}</b>"},
            {"type": "divider"},
            {"type": "text", "content": "Confirm to continue?"},
        ], emoji="✅")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data="CONFIRM"),
            InlineKeyboardButton("🔄 Change", callback_data="CHANGE"),
        ]])
        await _safe_edit(query, body, reply_markup=kb, parse_mode="HTML")

    elif data == "CHANGE":
        if u.get("gender"):
            await query.answer("⚠️ Gender already set.", show_alert=True)
            return
        await query.answer()
        body = box_simple("Select Gender", "Choose your gender:", emoji="🚻")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
            InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
        ]])
        await _safe_edit(query, body, reply_markup=kb, parse_mode="HTML")

    elif data == "CONFIRM":
        if u.get("gender"):
            await query.answer("⚠️ Gender already set.", show_alert=True)
            return
        await query.answer()
        u["gender"] = u.pop("temp", None)
        await save_user_to_db(uid, u)
        await context.bot.send_message(
            chat_id=uid, text="✅ Setup complete!", reply_markup=get_main_keyboard()
        )
        body = box_card("You are in!", [
            {"type": "line", "content": f"🎉 Hey {to_bold(name)}, profile set as <b>{u['gender']}</b>."},
            {"type": "divider"},
            {"type": "text", "content": "Ready to meet someone?"},
        ], emoji="🎉")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS"),
             InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
        ])
        await _safe_edit(query, body, reply_markup=kb, parse_mode="HTML")

    # ═══ MATCHING ═══
    elif data == "START_NEXT":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        await try_match(context, uid)

    elif data == "CANCEL_SEARCH":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        await cancel_search(context, uid)

    # ═══ RECONNECT / SAVE PARTNER (VIP) ═══
    elif data == "RECONNECT_PARTNER":
        await query.answer("🔄 Reconnecting...")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        from services.reconnect import reconnect_with_partner
        await reconnect_with_partner(context, uid)

    elif data == "SAVE_PARTNER":
        await query.answer("💾 Sending request...")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        from services.saved_partners import start_save_partner
        await start_save_partner(context, uid)

    elif data == "VIEW_SAVED":
        await query.answer()
        from services.saved_partners import list_saved_partners
        await list_saved_partners(context, uid)

    elif data.startswith("RECONNECT_IDX:"):
        try:
            idx = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("⚠️ Invalid.", show_alert=True)
            return
        saved = u.get("saved_partner_ids") or []
        if idx < 0 or idx >= len(saved):
            await query.answer("⚠️ Not found.", show_alert=True)
            return
        target_id = saved[idx]
        await query.answer(f"🔄 Reconnecting #{idx+1}...")
        u["_last_partner_id"] = target_id
        from services.reconnect import reconnect_with_partner
        await reconnect_with_partner(context, uid)

    elif data.startswith("UNSAVE_IDX:"):
        try:
            idx = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("⚠️ Invalid.", show_alert=True)
            return
        from services.saved_partners import unsave_partner_by_idx
        ok = await unsave_partner_by_idx(context, uid, idx)
        if ok:
            await query.answer(f"✅ Removed #{idx+1}")
            from services.saved_partners import list_saved_partners
            await list_saved_partners(context, uid)
        else:
            await query.answer("⚠️ Not found", show_alert=True)

    # ═══ SAVE PARTNER — nickname flow ═══
    elif data == "SAVE_NICK_SKIP":
        await query.answer("Skipping...")
        from services.saved_partners import finalize_save
        ok = await finalize_save(context, uid, nickname=None)
        if not ok:
            try:
                await query.edit_message_text(
                    "⚠️ Save failed. Please try again from /next → Save Partner."
                )
            except Exception:
                pass
        else:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

    elif data == "SAVE_NICK_CANCEL":
        await query.answer("Cancelled")
        u["awaiting_input"] = None
        u["_pending_save_partner_id"] = None
        await save_user_to_db(uid, u)
        try:
            await query.edit_message_text(
                "❌ Save request cancelled."
            )
        except Exception:
            pass

    elif data.startswith("SAVE_ACCEPT:"):
        token = data.split(":", 1)[1]
        if not token:
            await query.answer("⚠️ Invalid.", show_alert=True)
            return
        await query.answer("✅ Allowed!")
        from services.saved_partners import receiver_allow
        ok = await receiver_allow(context, uid, token)
        if not ok:
            await query.answer("⏰ Request expired.", show_alert=True)
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    elif data.startswith("SAVE_DECLINE:"):
        token = data.split(":", 1)[1]
        if not token:
            await query.answer("⚠️ Invalid.", show_alert=True)
            return
        await query.answer("❌ Declined")
        from services.saved_partners import receiver_decline
        await receiver_decline(context, uid, token)
        try:
            await query.edit_message_text("❌ You declined the save request.")
        except Exception:
            pass

    # ═══ DELETE ACCOUNT ═══
    elif data == "DELETE_CONFIRM":
        await query.answer("🗑️ Deleting...", show_alert=True)
        await _delete_user_account(uid)
        await _safe_edit(
            query,
            "🗑️  ✨  <b>Account Deleted</b>  ✨  🗑️\n"
            "▎\n"
            "▎ ✅ Your data has been removed.\n"
            "▎\n"
            "💡 <i>Send /start to begin fresh.</i>",
            parse_mode="HTML",
        )

    elif data == "DELETE_CANCEL":
        await query.answer("Cancelled")
        await _safe_edit(query, "❌ Deletion cancelled.")

    # ═══ STORE / VIP ═══
    elif data == "BUY_STORE":
        await query.answer()
        text, kb = get_store_markup(u)
        await _safe_edit(query, text, reply_markup=kb, parse_mode="HTML")

    elif data.startswith("BUY_STARS_"):
        plan_key = data.replace("BUY_STARS_", "")
        await query.answer()
        await send_vip_invoice(context, uid, plan_key)

    # ═══ SETTINGS ═══
    elif data == "OPEN_SETTINGS":
        await query.answer()
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    elif data == "BACK_DASHBOARD":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        body = box_card("Dashboard", [
            {"type": "text", "content": "🏠 Back at the dashboard."},
            {"type": "divider"},
            {"type": "quote", "content": "Use /next to find a partner, or open /settings."},
        ], emoji="🏠")
        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
             InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")],
            [InlineKeyboardButton("🎁 Invite & Earn", callback_data="OPEN_INVITE")],
        ])
        await context.bot.send_message(
            chat_id=uid, text=body, reply_markup=inline, parse_mode="HTML"
        )

    elif data == "CLOSE_SETTINGS":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass

    elif data == "TOGGLE_SET_MEDIA":
        await query.answer()
        u["confirm_media"] = not u.get("confirm_media", True)
        await save_user_to_db(uid, u)
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    elif data == "TOGGLE_SET_PRIVACY":
        await query.answer()
        u["profile_public"] = not u.get("profile_public", False)
        await save_user_to_db(uid, u)
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    elif data == "MENU_GENDER_PREF":
        await query.answer()
        body = box_simple("Match Filter", "Choose preferred gender:", emoji="🚻")
        await _safe_edit(query, body, reply_markup=get_gender_pref_kb(u), parse_mode="HTML")

    elif data.startswith("SET_PREF_"):
        pref = data.replace("SET_PREF_", "")
        if pref in ("Male", "Female") and not u.get("is_vip"):
            u["pref_gender"] = "Any"
            await save_user_to_db(uid, u)
            await query.answer("⚠️ VIP required! Reset to Any.", show_alert=True)
            text, kb = get_store_markup(u)
            await _safe_edit(query, text, reply_markup=kb, parse_mode="HTML")
            return
        await query.answer(f"✅ Set to {pref}")
        u["pref_gender"] = pref
        await save_user_to_db(uid, u)
        body = box_simple("Match Filter", "Choose preferred gender:", emoji="🚻")
        await _safe_edit(query, body, reply_markup=get_gender_pref_kb(u), parse_mode="HTML")

    elif data == "MENU_AGE":
        await query.answer()
        body = box_simple("Age", "Select age group:", emoji="🎂")
        await _safe_edit(query, body, reply_markup=get_age_kb(), parse_mode="HTML")

    elif data.startswith("SET_AGE_"):
        await query.answer()
        u["age"] = data.replace("SET_AGE_", "")
        await save_user_to_db(uid, u)
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    elif data == "MENU_COUNTRY":
        await query.answer()
        body = box_simple("Region", "Select location:", emoji="🌍")
        await _safe_edit(query, body, reply_markup=get_country_kb(), parse_mode="HTML")

    elif data.startswith("SET_CN_"):
        await query.answer()
        u["country"] = data.replace("SET_CN_", "")
        country = u["country"]
        tz_map = {
            "India": 5.5, "Pakistan": 5.0, "USA": -5.0,
            "UK": 0.0, "Canada": -5.0, "UAE/Gulf": 4.0,
            "Nepal": 5.75, "Global": 0.0,
        }
        for k, v in tz_map.items():
            if k in country:
                u["timezone_offset"] = v
                break
        await save_user_to_db(uid, u)
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    elif data == "EDIT_BIO":
        await query.answer()
        u["awaiting_input"] = "bio"
        body = box_simple("Edit Bio", "📝 Send your bio (max 120 chars):", emoji="📝")
        await _safe_edit(query, body, parse_mode="HTML")

    elif data == "MENU_INTERESTS":
        await query.answer()
        body = box_simple("Interests", "Select tags:", emoji="🏷️")
        await _safe_edit(query, body, reply_markup=get_interests_kb(u), parse_mode="HTML")

    elif data.startswith("TOGGLE_INT_"):
        try:
            idx = int(data.replace("TOGGLE_INT_", ""))
            if not (0 <= idx < len(AVAILABLE_INTERESTS)):
                raise ValueError
        except (ValueError, TypeError):
            await query.answer("⚠️ Invalid.", show_alert=True)
            return
        await query.answer()
        item = AVAILABLE_INTERESTS[idx]
        if item in u["interests"]:
            u["interests"].remove(item)
        else:
            u["interests"].append(item)
        await save_user_to_db(uid, u)
        body = box_simple("Interests", "Select tags:", emoji="🏷️")
        await _safe_edit(query, body, reply_markup=get_interests_kb(u), parse_mode="HTML")

    # ═══ LANGUAGE ═══
    elif data == "MENU_LANGUAGE":
        await query.answer()
        await _safe_edit(
            query,
            "🌐 <b>Choose Language</b>",
            reply_markup=get_language_kb(),
            parse_mode="HTML",
        )

    elif data.startswith("SET_LANG_"):
        lang = data.replace("SET_LANG_", "")
        if lang not in SUPPORTED_LANGUAGES:
            await query.answer("Unsupported", show_alert=True)
            return
        u["language"] = lang
        await save_user_to_db(uid, u)
        await query.answer(f"✅ Language: {lang.upper()}")
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    # ═══ TIMEZONE ═══
    elif data == "MENU_TIMEZONE":
        await query.answer()
        await _safe_edit(
            query,
            "🕐 <b>Choose Timezone</b>",
            reply_markup=get_timezone_kb(),
            parse_mode="HTML",
        )

    elif data.startswith("SET_TZ_"):
        try:
            offset = float(data.replace("SET_TZ_", ""))
        except ValueError:
            await query.answer("⚠️ Invalid", show_alert=True)
            return
        u["timezone_offset"] = offset
        u["timezone_set_manually"] = True
        await save_user_to_db(uid, u)
        await query.answer(f"✅ Timezone: UTC{offset:+g}")
        await _safe_edit(
            query,
            get_settings_text(u),
            reply_markup=get_settings_main_kb(u),
            parse_mode="HTML",
        )

    # ═══ THEME ═══
    elif data == "MENU_THEME":
        await query.answer()
        await _safe_edit(
            query,
            "🎨 <b>Choose Chat Theme</b>",
            reply_markup=get_theme_kb(u.get("theme")),
            parse_mode="HTML",
        )

    elif data.startswith("THEME_"):
        theme = data.replace("THEME_", "")
        if theme == "random":
            theme = random.choice(["party", "cosmic", "fire", "sakura", "cute", "royal"])
        u["theme"] = theme
        await save_user_to_db(uid, u)
        await query.answer(f"✅ Theme: {theme.title()}")
        await _safe_edit(
            query,
            f"🎨 Theme set to <b>{theme.title()}</b>\n\n"
            f"<i>Applies to your next match card.</i>",
            reply_markup=get_theme_kb(theme),
            parse_mode="HTML",
        )

    # ═══ GENDER LOCK ═══
    elif data == "CHANGE_GENDER":
        await query.answer("⚠️ Gender can only be set once.", show_alert=True)

    elif data.startswith("SET_GENDER_"):
        await query.answer("⚠️ Gender cannot be changed.", show_alert=True)

    # ═══ MEDIA ═══
    elif data.startswith("MEDIA_ACCEPT:"):
        await query.answer()
        try:
            mid = int(data.split(":", 1)[1])
        except Exception:
            await _safe_edit(query, "⚠️ Invalid.")
            return
        pm_map = u.get("pending_media") or {}
        pm = pm_map.get(mid)
        if pm and u.get("partner") == pm["from_id"]:
            try:
                await context.bot.copy_message(
                    chat_id=uid, from_chat_id=pm["from_id"], message_id=mid
                )
                await safe_send(context, pm["from_id"], "✅ Partner accepted your media.")
            except Exception:
                await safe_send(context, pm["from_id"], "❌ Media expired.")
            pm_map.pop(mid, None)
            u["pending_media"] = pm_map
            from state import pending_media_timeouts
            pending_media_timeouts.pop((pm["from_id"], mid), None)
            await _safe_edit(query, "✅ Media accepted.")
        else:
            await _safe_edit(query, "⚠️ Expired or session ended.")

    elif data.startswith("MEDIA_DECLINE:"):
        await query.answer()
        try:
            mid = int(data.split(":", 1)[1])
        except Exception:
            await _safe_edit(query, "⚠️ Invalid.")
            return
        pm_map = u.get("pending_media") or {}
        pm = pm_map.get(mid)
        if pm and u.get("partner") == pm["from_id"]:
            await safe_send(context, pm["from_id"], "❌ Partner declined your media.")
            pm_map.pop(mid, None)
            u["pending_media"] = pm_map
            from state import pending_media_timeouts
            pending_media_timeouts.pop((pm["from_id"], mid), None)
            await _safe_edit(query, "🚫 Media declined.")
        else:
            await _safe_edit(query, "⚠️ No longer active.")

    # ═══ BROADCAST CONFIRM ═══
    elif data == "BC_CONFIRM":
        if not await is_owner_or_admin(uid):
            await query.answer("Unauthorized", show_alert=True)
            return
        message = context.user_data.get("pending_broadcast")
        if not message:
            await query.answer("No pending broadcast.", show_alert=True)
            return
        context.user_data.pop("pending_broadcast", None)
        await _safe_edit(query, "📢 Broadcasting... please wait.")
        sent, failed = await execute_broadcast(
            context, message,
            admin_uid=uid,
        )
        await safe_send(
            context, uid,
            box_card("Broadcast Done", [
                {"type": "kv", "items": [
                    ("✅ Sent", f"<b>{sent}</b>"),
                    ("❌ Failed", f"<b>{failed}</b>"),
                ]},
            ], emoji="📢"), parse_mode="HTML",
        )

    elif data == "BC_CANCEL":
        context.user_data.pop("pending_broadcast", None)
        await _safe_edit(query, "❌ Broadcast cancelled.")

    # ═══ FORCE DELETE CONFIRMATION ═══
    elif data.startswith("FD_CONFIRM:"):
        if not await is_owner_or_admin(uid):
            await query.answer("Unauthorized", show_alert=True)
            return

        try:
            target_id = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("⚠️ Invalid.", show_alert=True)
            return

        if target_id == uid:
            await query.answer("Cannot delete yourself!", show_alert=True)
            return

        await query.answer("🗑️ Deleting...")

        await _safe_edit(query, "⏳ Deleting user... please wait.")

        try:
            from services.user_deletion import delete_user_completely
            stats = await asyncio.wait_for(
                delete_user_completely(target_id),
                timeout=FORCE_DELETE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"⏱️ Force delete timed out for {target_id}")
            stats = {
                "was_in_memory": True,
                "was_in_queue": False,
                "was_in_db": False,
                "partner_notified": False,
                "db_deleted": False,
                "memory_cleaned": True,
                "error": f"Timed out ({FORCE_DELETE_TIMEOUT}s) — "
                         f"memory cleared, DB retry in background",
            }
        except Exception as e:
            logger.error(f"Force delete error for {target_id}: {e}", exc_info=True)
            stats = {
                "was_in_memory": False,
                "was_in_queue": False,
                "was_in_db": False,
                "partner_notified": False,
                "db_deleted": False,
                "memory_cleaned": False,
                "error": str(e),
            }

        lines = [
            f"🆔 User : <code>{target_id}</code>",
            "",
            "📊 <b>Delete Summary</b>",
        ]
        lines.append(
            f"  ├ Memory : {'✅ Cleared' if stats.get('memory_cleaned') else '❌ Failed'}"
        )
        lines.append(
            f"  ├ Queue : {'✅ Removed' if stats.get('was_in_queue') else '⚪ Not in queue'}"
        )
        lines.append(
            f"  ├ Database : {'✅ Deleted' if stats.get('db_deleted') else '⚪ Not found'}"
        )
        lines.append(
            f"  └ Partner : {'✅ Cleared' if stats.get('partner_notified') else '⚪ No partner'}"
        )

        if stats.get("error"):
            lines.append("")
            lines.append(f"⚠️ Note: <code>{stats['error']}</code>")

        await _safe_edit(
            query,
            _sidebar_styled("Deleted", "🗑️", lines),
            parse_mode="HTML",
        )

    elif data == "FD_CANCEL":
        context.user_data.pop("forcedelete_target", None)
        await query.answer("Cancelled")
        await _safe_edit(query, "❌ Force delete cancelled.")

    # ═══ IN-CHAT ACTION BUTTONS ═══
    elif data == "CHAT_NEXT":
        await query.answer()
        await try_match(context, uid)
    elif data == "CHAT_END":
        await query.answer()
        await end_chat_internal(context, uid)
    elif data == "CHAT_REPORT":
        await query.answer()
        await report_internal(context, uid)
    elif data == "CHAT_BLOCK":
        await query.answer()
        await block_internal(context, uid)
    elif data == "CHAT_VOICE":
        await query.answer("🎙️ Creating voice room...")
        await create_voice_room(context, uid)

    # ═══ FEEDBACK ═══
    elif data in ("FB_GOOD", "FB_BAD"):
        await query.answer("Thanks for your feedback!")
        u.setdefault("match_feedback", []).append({
            "fb": data, "at": str(utcnow()),
        })
        u["match_feedback"] = u["match_feedback"][-50:]
        await save_user_to_db(uid, u)
        await _safe_edit(
            query,
            "✅ Feedback recorded!" if data == "FB_GOOD" else "📝 Feedback noted."
        )

    # ═══ INVITE ═══
    elif data == "OPEN_INVITE":
        await query.answer()
        from services.referral import ensure_referral_code, _get_next_milestone
        code = await ensure_referral_code(uid, u)
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"
        count = 0
        if users_collection is not None:
            count = await users_collection.count_documents({"referred_by": uid})

        next_ms = _get_next_milestone(count)

        text = (
            "🎁  ✨  <b>Invite & Earn</b>  ✨  🎁\n"
            "▎\n"
            f"▎ 👥 Referrals : <b>{count}</b>\n"
        )
        if next_ms:
            nt, nd, ne, nl = next_ms
            text += (
                f"▎ 🎯 Next : {ne} <b>{nl}</b>\n"
                f"▎ 📈 Need : <b>{nt - count}</b> more\n"
            )
        else:
            text += "▎ 👑 <b>All milestones reached!</b>\n"

        text += (
            "▎\n"
            f"▎ 🔗 <code>{link}</code>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share",
                                  url=f"https://t.me/share/url?url={link}&text=Join%20SparkTalks!")],
            [InlineKeyboardButton("📋 Copy", callback_data=f"COPY_REF:{code}")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="REF_LEADERBOARD")],
            [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
        ])
        await _safe_edit(query, text, reply_markup=kb, parse_mode="HTML")

    elif data.startswith("COPY_REF:"):
        code = data.split(":", 1)[1]
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"
        await query.answer(f"📋 Link: {link}", show_alert=True)

    elif data == "REF_LEADERBOARD":
        await query.answer()
        from services.referral import send_leaderboard
        await send_leaderboard(query, context, uid)

    else:
        try:
            await query.answer()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# DELETE ACCOUNT HELPER
# ══════════════════════════════════════════════════════════════

async def _delete_user_account(uid: int):
    """Remove user from memory + DB. Uses safe timeout wrapper."""
    from state import (
        users, queue, queue_set, queue_lock,
        last_next_time, muted_users, saved_partners, reconnect_daily,
        pending_save_requests,
    )
    from database import users_collection, _pending_writes

    users.pop(uid, None)
    _pending_writes.pop(uid, None)
    last_next_time.pop(uid, None)
    muted_users.pop(uid, None)
    saved_partners.pop(uid, None)
    reconnect_daily.pop(uid, None)
    pending_save_requests.pop(uid, None)

    try:
        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
    except Exception as e:
        logger.debug(f"Queue removal failed for {uid}: {e}")

    if users_collection is not None:
        try:
            await asyncio.wait_for(
                users_collection.delete_one({"user_id": uid}),
                timeout=10.0,
            )
            logger.info(f"🗑️ Self-delete: user {uid} removed from DB")
        except asyncio.TimeoutError:
            logger.error(f"⏱️ Self-delete DB timeout for {uid}")
        except Exception as e:
            logger.error(f"Self-delete DB failed for {uid}: {e}", exc_info=True)