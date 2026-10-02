from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import AVAILABLE_INTERESTS
from state import users
from database import get_user, save_user_to_db
from utils import spark_card, safe_send
from keyboards import (
    get_main_keyboard, get_store_markup, get_settings_text,
    get_settings_main_kb, get_gender_pref_kb, get_age_kb,
    get_country_kb, get_interests_kb
)
from services.matching import try_match, end_chat_internal, report_internal, block_internal, disconnect
from services.vip import notify_owner_purchase, send_vip_invoice


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

    if data in ("G_MALE", "G_FEMALE"):
        await query.answer()
        if u.get("gender"):
            return
        u["temp"] = "Male" if data == "G_MALE" else "Female"
        msg = spark_card("Almost there", f"You selected: <b>{u['temp']}</b>\n\nConfirm to continue on SparkTalks?", "SparkTalks")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data="CONFIRM"),
            InlineKeyboardButton("🔄 Change", callback_data="CHANGE")
        ]])
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="HTML")

    elif data == "CHANGE":
        await query.answer()
        if u.get("gender"):
            return
        msg = spark_card("Select Gender", "Choose your gender:", "SparkTalks")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="G_MALE"),
            InlineKeyboardButton("👩🏻 Female", callback_data="G_FEMALE")
        ]])
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="HTML")

    elif data == "CONFIRM":
        await query.answer()
        u["gender"] = u.pop("temp", None)
        await save_user_to_db(uid, u)
        await context.bot.send_message(chat_id=uid, text="✅ Setup complete!", reply_markup=get_main_keyboard())
        msg = spark_card(
            "You're in!",
            f"🎉 Hey {name}, your profile is set as <b>{u['gender']}</b>.\n\n"
            f"You're all set on SparkTalks. Ready to meet someone?",
            "SparkTalks"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Find Partner", callback_data="START_NEXT")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="OPEN_SETTINGS"),
             InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")]
        ])
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="HTML")

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
        await notify_owner_purchase(context, query.from_user, plan_key)
        await send_vip_invoice(context, uid, plan_key)

    elif data == "OPEN_SETTINGS":
        await query.answer()
        await query.edit_message_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")

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
        await query.edit_message_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")

    elif data == "TOGGLE_SET_PRIVACY":
        await query.answer()
        u["profile_public"] = not u.get("profile_public", False)
        await save_user_to_db(uid, u)
        await query.edit_message_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")

    elif data == "MENU_GENDER_PREF":
        await query.answer()
        await query.edit_message_text(spark_card("Match Filter", "Choose preferred gender:"), reply_markup=get_gender_pref_kb(u), parse_mode="HTML")

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
        await query.edit_message_text(spark_card("Match Filter", "Choose preferred gender:"), reply_markup=get_gender_pref_kb(u), parse_mode="HTML")

    elif data == "MENU_AGE":
        await query.answer()
        await query.edit_message_text(spark_card("Age", "Select age group:"), reply_markup=get_age_kb(), parse_mode="HTML")

    elif data.startswith("SET_AGE_"):
        await query.answer()
        u["age"] = data.replace("SET_AGE_", "")
        await save_user_to_db(uid, u)
        await query.edit_message_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")

    elif data == "MENU_COUNTRY":
        await query.answer()
        await query.edit_message_text(spark_card("Region", "Select location:"), reply_markup=get_country_kb(), parse_mode="HTML")

    elif data.startswith("SET_CN_"):
        await query.answer()
        u["country"] = data.replace("SET_CN_", "")
        await save_user_to_db(uid, u)
        await query.edit_message_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")

    elif data == "EDIT_BIO":
        await query.answer()
        u["awaiting_input"] = "bio"
        await query.edit_message_text(spark_card("Edit Bio", "📝 Send your bio (max 120 chars):"), parse_mode="HTML")

    elif data == "MENU_INTERESTS":
        await query.answer()
        await query.edit_message_text(spark_card("Interests", "Select tags:"), reply_markup=get_interests_kb(u), parse_mode="HTML")

    elif data.startswith("TOGGLE_INT_"):
        await query.answer()
        idx = int(data.replace("TOGGLE_INT_", ""))
        item = AVAILABLE_INTERESTS[idx]
        if item in u["interests"]:
            u["interests"].remove(item)
        else:
            u["interests"].append(item)
        await save_user_to_db(uid, u)
        await query.edit_message_text(spark_card("Interests", "Select tags:"), reply_markup=get_interests_kb(u), parse_mode="HTML")

    elif data == "CHANGE_GENDER":
        await query.answer()
        msg = spark_card("Change Gender", "Select your new gender:", "SparkTalks")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨🏻 Male", callback_data="SET_GENDER_Male"),
            InlineKeyboardButton("👩🏻 Female", callback_data="SET_GENDER_Female")
        ], [
            InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")
        ]])
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="HTML")

    elif data.startswith("SET_GENDER_"):
        new_gender = data.replace("SET_GENDER_", "")
        await query.answer(f"✅ Gender changed to {new_gender}")
        u["gender"] = new_gender
        await save_user_to_db(uid, u)
        await query.edit_message_text(get_settings_text(u), reply_markup=get_settings_main_kb(u), parse_mode="HTML")

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
                await context.bot.copy_message(chat_id=uid, from_chat_id=pm["from_id"], message_id=mid)
                await safe_send(context, pm["from_id"], "✅ Partner accepted your media.")
            except Exception as e:
                from utils import logger
                logger.error(f"Media relay error: {e}")
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

    elif data == "CHAT_NEXT":
        await query.answer()
        if u.get("partner"):
            await disconnect(context, uid, u["partner"])
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
