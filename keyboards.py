from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)

from config import (
    BTN_FIND, BTN_PROFILE, BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK,
    AVAILABLE_INTERESTS, VIP_PLANS
)
from utils import spark_card, get_owner_link, to_bold, to_smallcaps


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_FIND)],
         [KeyboardButton(BTN_PROFILE)]],
        resize_keyboard=True
    )


def get_chat_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_NEXT), KeyboardButton(BTN_END)],
         [KeyboardButton(BTN_REPORT), KeyboardButton(BTN_BLOCK)]],
        resize_keyboard=True
    )


def get_store_markup(u: dict = None):
    is_vip = u.get("is_vip", False) if u else False
    status_str = f"⭐ {u.get('vip_tier_name', 'VIP Active')}" if is_vip else "Free Member"

    body = (
        f"💎 <b>{to_bold('SparkTalks VIP')}</b>\n\n"
        f"<i>Unlock the full experience and stand out from the crowd.</i>\n\n"
        f"🌟 <b>Your Status:</b> {status_str}\n\n"
        f"✨ <b>{to_bold('VIP Benefits')}</b>\n"
        f"• 🚻 Gender Filter (Male / Female / Anyone)\n"
        f"• ⚡ Priority Matching (faster partners)\n"
        f"• 👑 Exclusive VIP Badge\n"
        f"• 🔄 Unlimited Next without cooldown\n"
        f"• 🛡️ Higher limits & better experience\n\n"
        f"👑 <b>{to_bold('Available Plans')}</b>\n\n"
        f"🚀 <b>{to_bold('Sprint')}</b> · 14 Days\n"
        f"   ₹99  ·  $1.99  ·  60⭐\n\n"
        f"🥇 <b>{to_bold('Gold')}</b> · 1 Month\n"
        f"   ₹179  ·  $3.49  ·  110⭐\n\n"
        f"💎 <b>{to_bold('Diamond')}</b> · 3 Months\n"
        f"   ₹449  ·  $8.49  ·  250⭐\n\n"
        f"🔥 <b>{to_bold('Master')}</b> · 6 Months\n"
        f"   ₹799  ·  $14.99  ·  450⭐\n\n"
        f"<i>Tap a plan to pay with Telegram Stars\nor contact owner for UPI / USD.</i>"
    )

    inquiry = (
        "Hello! I am interested in purchasing a SparkTalks VIP membership.\n\n"
        "Please share the available payment options (UPI / USD / Other) "
        "and guide me on how to complete the purchase. Thank you!"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Sprint · 60⭐", callback_data="BUY_STARS_PLAN_14D"),
         InlineKeyboardButton("🥇 Gold · 110⭐", callback_data="BUY_STARS_PLAN_1M")],
        [InlineKeyboardButton("💎 Diamond · 250⭐", callback_data="BUY_STARS_PLAN_3M"),
         InlineKeyboardButton("🔥 Master · 450⭐", callback_data="BUY_STARS_PLAN_6M")],
        [InlineKeyboardButton("💬 Contact Owner (UPI / USD)", url=get_owner_link(inquiry))],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")]
    ])
    return spark_card("VIP Store", body, "Official Support"), kb


def get_profile_text(u: dict) -> str:
    import html
    status = "🟢 Live Connected" if u.get("partner") else (
        "🟡 Searching" if u.get("state") == "SEARCHING" else "⚪ Idle"
    )
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost Mode"

    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y %H:%M") if exp else "Active"
        vip = f"⭐ {u.get('vip_tier_name', 'VIP')} (Till: {exp_str})"
    else:
        vip = "Free Member"

    total_chats = u.get("total_chats", 0)
    total_matches = u.get("total_matches", 0)

    body = (
        f"❖ <b>{to_bold('Identity')}</b>\n"
        f"  👤 Gender: {u.get('gender') or 'Not set'}\n"
        f"  🎂 Age: {age}\n"
        f"  🌍 Region: {country}\n"
        f"  🌟 Membership: {vip}\n\n"
        f"❖ <b>{to_bold('About')}</b>\n"
        f"  📝 Bio: {bio}\n"
        f"  🏷️ Interests: {interests}\n"
        f"  🛡️ Privacy: {visibility}\n"
        f"  ⚡ Status: {status}\n\n"
        f"❖ <b>{to_bold('Statistics')}</b>\n"
        f"  • Total Chats: {total_chats}\n"
        f"  • Total Matches: {total_matches}"
    )
    return spark_card("Your Profile", body, "Use /settings to update")


def get_settings_text(u: dict) -> str:
    import html
    media = "🛡️ Ask Confirmation" if u.get("confirm_media", True) else "⚡ Auto-Receive"
    pref = u.get("pref_gender", "Any")
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost Mode"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = html.escape(u.get("bio") or "No bio.")
    interests = ", ".join(u.get("interests", [])) or "None"

    body = (
        f"❖ <b>{to_bold('Security & Matching')}</b>\n"
        f"  • Media Protection: {media}\n"
        f"  • Preferred Match: {pref}\n\n"
        f"❖ <b>{to_bold('Your Profile')}</b>\n"
        f"  • Mode: {visibility}\n"
        f"  • Age: {age}\n"
        f"  • Region: {country}\n"
        f"  • Bio: {bio}\n"
        f"  • Tags: {interests}"
    )
    return spark_card("Settings", body, "Tap an option below")


def get_settings_main_kb(u: dict):
    media_btn = "🛡️ Media: ON" if u.get("confirm_media", True) else "⚡ Media: Direct"
    privacy_btn = "🔒 Ghost: ON" if not u.get("profile_public") else "👁️ Ghost: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(media_btn, callback_data="TOGGLE_SET_MEDIA"),
         InlineKeyboardButton("🚻 Match Filter", callback_data="MENU_GENDER_PREF")],
        [InlineKeyboardButton(privacy_btn, callback_data="TOGGLE_SET_PRIVACY")],
        [InlineKeyboardButton("🎂 Age", callback_data="MENU_AGE"),
         InlineKeyboardButton("🌍 Region", callback_data="MENU_COUNTRY")],
        [InlineKeyboardButton("📝 Bio", callback_data="EDIT_BIO"),
         InlineKeyboardButton("🏷️ Interests", callback_data="MENU_INTERESTS")],
        [InlineKeyboardButton("🔄 Change Gender", callback_data="CHANGE_GENDER")],
        [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
        [InlineKeyboardButton("❌ Close", callback_data="CLOSE_SETTINGS")]
    ])


def get_gender_pref_kb(u: dict):
    pref = u.get("pref_gender", "Any")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Males Only" if pref == "Male" else "👨🏻 Males Only", callback_data="SET_PREF_Male"),
         InlineKeyboardButton("✅ Females Only" if pref == "Female" else "👩🏻 Females Only", callback_data="SET_PREF_Female")],
        [InlineKeyboardButton("✅ Anyone" if pref == "Any" else "🌐 Anyone", callback_data="SET_PREF_Any")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")]
    ])


def get_age_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("18 - 21", callback_data="SET_AGE_18-21"),
         InlineKeyboardButton("22 - 25", callback_data="SET_AGE_22-25")],
        [InlineKeyboardButton("26 - 30", callback_data="SET_AGE_26-30"),
         InlineKeyboardButton("31+", callback_data="SET_AGE_31+")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")]
    ])


def get_country_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 India", callback_data="SET_CN_🇮🇳 India"),
         InlineKeyboardButton("🇵🇰 Pakistan", callback_data="SET_CN_🇵🇰 Pakistan")],
        [InlineKeyboardButton("🇺🇸 USA", callback_data="SET_CN_🇺🇸 USA"),
         InlineKeyboardButton("🇬🇧 UK", callback_data="SET_CN_🇬🇧 UK")],
        [InlineKeyboardButton("🇨🇦 Canada", callback_data="SET_CN_🇨🇦 Canada"),
         InlineKeyboardButton("🇦🇪 UAE/Gulf", callback_data="SET_CN_🇦🇪 UAE/Gulf")],
        [InlineKeyboardButton("🇳🇵 Nepal", callback_data="SET_CN_🇳🇵 Nepal"),
         InlineKeyboardButton("🌐 Global", callback_data="SET_CN_🌐 Global")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")]
    ])


def get_interests_kb(u: dict):
    selected = u.get("interests", [])
    buttons, row = [], []
    for idx, item in enumerate(AVAILABLE_INTERESTS):
        prefix = "✅ " if item in selected else "➕ "
        row.append(InlineKeyboardButton(f"{prefix}{item}", callback_data=f"TOGGLE_INT_{idx}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(buttons)