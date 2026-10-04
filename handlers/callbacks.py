from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import AVAILABLE_INTERESTS
from state import users
from database import get_user, save_user_to_db, is_owner_or_admin
from utils import box_card, box_simple, safe_send, to_bold
from keyboards import (
    get_main_keyboard, get_store_markup, get_settings_text,
    get_settings_main_kb, get_gender_pref_kb, get_age_kb,
    get_country_kb, get_interests_kb,
)
from services.matching import (
    try_match, end_chat_internal, report_internal, block_internal,
    disconnect, cancel_search,
)
from services.vip import send_vip_invoice
from services.broadcast import execute_broadcast


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

    # ─── Gender Selection ───
    if data in ("G_MALE", "G_FEMALE"):
        await query.answer()
        if u.get("gender"):
            await query.answer("⚠️ Gender already set.", show_alert=True)
            return
        u["temp"] = "Male" if data == "G_MALE" else "Female"
        body = box_card(
            "Almost there",
            [
                {"type": "text", "content": f"You selected: <b>{u['temp']}</b>"},
                {"type": "divider"},
                {"type": "text", "content": "Confirm to continue on SparkTalks?"},
            ],
            emoji="✅",
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data="CONFIRM"),
            InlineKeyboardButton("🔄 Change", callback_data="CHANGE"),
        ]])
        await query.edit_message_text(body, reply_markup=kb, parse_mode="HTML")

    elif data == "CHANGE":
        await query.answer()
        if u.get("gender"):
            await query.answer("⚠️ Gender already set.", show_alert=True)
            return
        body = box_simple("Select Gender", "Choose your gender:", emoji="🚻")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
            InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE"),
        ]])
        await query.edit_message_text(body, reply_markup=kb, parse_mode="HTML")

    elif data == "CONFIRM":
        await query.answer()
        if u.get("gender"):
            await query.answer("⚠️ Gender already set.", show_alert=True)
            return
        u["gender"] = u.pop("temp", None)
        await save_user_to_db(uid, u)
        await context.bot.send_message(
            chat_id=uid, text="✅ Setup complete!", reply_markup=get_main_keyboard()
        )
        body = box_card(
            "You are in!",
            [
                {"type": "line", "content": f"🎉 Hey {to_bold(name)}, your profile is set as <b>{u['gender']}</b>."},
                {"type": "divider"},
                {"type": "text", "content": "You are all set. Ready to meet someone?"},
            ],
            emoji="🎉",
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS"),
             InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
        ])
        await query.edit_message_text(body, reply_markup=kb, parse_mode="HTML")

    # ─── Match / Store ───
    elif data == "START_NEXT":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        await try_match(context, uid)

    elif data == "BUY_STORE":
        await query.answer()
        text, kb = get_store_markup(u)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    elif data.startswith("BUY_STARS_"):
        plan_key = data.replace("BUY_STARS_", "")
        await query.answer()
        await send_vip_invoice(context, uid, plan_key)

    # ─── Settings ───
    elif data == "OPEN_SETTINGS":
        await query.answer()
        await query.edit_message_text(
            get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML"
        )

    elif data == "BACK_DASHBOARD":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        body = box_card("Dashboard", [
            {"type": "text", "content": "🏠 You are back at the dashboard."},
            {"type": "divider"},
            {"type": "quote", "content": "Use /next to find a partner, or open /settings."},
        ], emoji="🏠")
        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE"),
             InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS")],
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
        await query.edit_message_text(
            get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML"
        )

    elif data == "TOGGLE_SET_PRIVACY":
        await query.answer()
        u["profile_public"] = not u.get("profile_public", False)
        await save_user_to_db(uid, u)
        await query.edit_message_text(
            get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML"
        )

    # ─── Match Filter ───
    elif data == "MENU_GENDER_PREF":
        await query.answer()
        body = box_simple("Match Filter", "Choose preferred gender:", emoji="🚻")
        await query.edit_message_text(body, reply_markup=get_gender_pref_kb(u), parse_mode="HTML")

    elif data.startswith("SET_PREF_"):
        pref = data.replace("SET_PREF_", "")
        if pref in ("Male", "Female") and not u.get("is_vip"):
            u["pref_gender"] = "Any"
            await save_user_to_db(uid, u)
            await query.answer("⚠️ VIP required! Reset to Any.", show_alert=True)
            text, kb = get_store_markup(u)
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
            return
        await query.answer(f"✅ Set to {pref}")
        u["pref_gender"] = pref
        await save_user_to_db(uid, u)
        body = box_simple("Match Filter", "Choose preferred gender:", emoji="🚻")
        await query.edit_message_text(body, reply_markup=get_gender_pref_kb(u), parse_mode="HTML")

    # ─── Age ───
    elif data == "MENU_AGE":
        await query.answer()
        body = box_simple("Age", "Select age group:", emoji="🎂")
        await query.edit_message_text(body, reply_markup=get_age_kb(), parse_mode="HTML")

    elif data.startswith("SET_AGE_"):
        await query.answer()
        u["age"] = data.replace("SET_AGE_", "")
        await save_user_to_db(uid, u)
        await query.edit_message_text(
            get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML"
        )

    # ─── Country ───
    elif data == "MENU_COUNTRY":
        await query.answer()
        body = box_simple("Region", "Select location:", emoji="🌍")
        await query.edit_message_text(body, reply_markup=get_country_kb(), parse_mode="HTML")

    elif data.startswith("SET_CN_"):
        await query.answer()
        u["country"] = data.replace("SET_CN_", "")
        await save_user_to_db(uid, u)
        await query.edit_message_text(
            get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML"
        )

    # ─── Bio ───
    elif data == "EDIT_BIO":
        await query.answer()
        u["awaiting_input"] = "bio"
        body = box_simple("Edit Bio", "📝 Send your bio (max 120 chars):", emoji="📝")
        await query.edit_message_text(body, parse_mode="HTML")

    # ─── Interests ───
    elif data == "MENU_INTERESTS":
        await query.answer()
        body = box_simple("Interests", "Select tags:", emoji="🏷️")
        await query.edit_message_text(body, reply_markup=get_interests_kb(u), parse_mode="HTML")

    elif data.startswith("TOGGLE_INT_"):
        try:
            idx = int(data.replace("TOGGLE_INT_", ""))
            if not (0 <= idx < len(AVAILABLE_INTERESTS)):
                raise ValueError("out of range")
        except (ValueError, TypeError):
            await query.answer("⚠️ Invalid selection.", show_alert=True)
            return
        await query.answer()
        item = AVAILABLE_INTERESTS[idx]
        if item in u["interests"]:
            u["interests"].remove(item)
        else:
            u["interests"].append(item)
        await save_user_to_db(uid, u)
        body = box_simple("Interests", "Select tags:", emoji="🏷️")
        await query.edit_message_text(body, reply_markup=get_interests_kb(u), parse_mode="HTML")

    # ─── Gender Change blocked ───
    elif data == "CHANGE_GENDER":
        await query.answer("⚠️ Gender can only be set once.", show_alert=True)

    elif data.startswith("SET_GENDER_"):
        await query.answer("⚠️ Gender cannot be changed.", show_alert=True)

    # ─── Cancel Search ───
    elif data == "CANCEL_SEARCH":
        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        await cancel_search(context, uid)

    # ─── Media Handling ───
    elif data.startswith("MEDIA_ACCEPT:"):
        await query.answer()
        try:
            mid = int(data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("⚠️ Invalid.")
            return
        pm_map = u.get("pending_media") or {}
        pm = pm_map.get(mid)
        if pm and u.get("partner") == pm["from_id"]:
            try:
                await context.bot.copy_message(
                    chat_id=uid, from_chat_id=pm["from_id"], message_id=mid
                )
                await safe_send(context, pm["from_id"], "✅ Partner accepted your media.")
            except Exception as e:
                from utils import logger
                logger.error(f"Media relay error: {e}")
                await safe_send(context, pm["from_id"], "❌ Could not deliver media (expired).")
            pm_map.pop(mid, None)
            u["pending_media"] = pm_map
            await query.edit_message_text("✅ Media accepted.")
        else:
            await query.edit_message_text("⚠️ Expired or session ended.")

    elif data.startswith("MEDIA_DECLINE:"):
        await query.answer()
        try:
            mid = int(data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("⚠️ Invalid.")
            return
        pm_map = u.get("pending_media") or {}
        pm = pm_map.get(mid)
        if pm and u.get("partner") == pm["from_id"]:
            await safe_send(context, pm["from_id"], "❌ Partner declined your media.")
            pm_map.pop(mid, None)
            u["pending_media"] = pm_map
            await query.edit_message_text("🚫 Media declined.")
        else:
            await query.edit_message_text("⚠️ No longer active.")

    # ─── Broadcast confirm / cancel ───
    elif data == "BC_CONFIRM":
        if not await is_owner_or_admin(uid):
            await query.answer("Unauthorized", show_alert=True)
            return
        message = context.user_data.get("pending_broadcast")
        if not message:
            await query.answer("No pending broadcast.", show_alert=True)
            return
        context.user_data.pop("pending_broadcast", None)
        await query.edit_message_text("📢 Broadcasting... please wait.")
        sent, failed = await execute_broadcast(context, message)
        await safe_send(
            context, uid,
            box_card("Broadcast Done", [
                {"type": "kv", "items": [
                    (f"✅ {to_bold('Sent')}", f"<b>{sent}</b>"),
                    (f"❌ {to_bold('Failed')}", f"<b>{failed}</b>"),
                ]},
            ], emoji="📢"),
            parse_mode="HTML",
        )

    elif data == "BC_CANCEL":
        context.user_data.pop("pending_broadcast", None)
        await query.edit_message_text("❌ Broadcast cancelled.")

    # ─── Chat Controls ───
    elif data == "CHAT_NEXT":
        await query.answer()
        # try_match handles disconnect + requeue internally
        # (skips "Chat Ended" for this user automatically)
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