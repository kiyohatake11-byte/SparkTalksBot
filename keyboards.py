from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
import html as _html

from config import (
    BTN_FIND, BTN_PROFILE, BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK,
    BTN_VOICE, BTN_VOICE_LOCKED,
    AVAILABLE_INTERESTS, TIMEZONE_OPTIONS,
)
from utils import get_owner_link


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_FIND, style="primary")],
         [KeyboardButton(BTN_PROFILE)]],
        resize_keyboard=True,
    )


def get_chat_keyboard(u: dict = None):
    is_vip = u.get("is_vip", False) if u else False
    voice_btn = BTN_VOICE if is_vip else BTN_VOICE_LOCKED

    if is_vip:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(BTN_NEXT, style="primary"),
              KeyboardButton(BTN_END, style="danger")],
             [KeyboardButton(voice_btn, style="primary"),
              KeyboardButton(BTN_REPORT, style="danger")],
             [KeyboardButton(BTN_BLOCK, style="danger")]],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_NEXT, style="primary"),
          KeyboardButton(BTN_END, style="danger")],
         [KeyboardButton(voice_btn, style="primary"),
          KeyboardButton(BTN_REPORT, style="danger")]],
        resize_keyboard=True,
    )


def get_store_markup(u: dict = None):
    is_vip = u.get("is_vip", False) if u else False
    tier = (u.get("vip_tier_name") or "VIP Active") if u else "VIP Active"
    status = f"👑 {tier}" if is_vip else "⚪ Free Member"

    text = (
        "🛍️  ✨  <b>VIP Store</b>  ✨  🛍️\n"
        "▎\n"
        f"▎ ⚡ Status : {status}\n"
        "▎\n"
        "▎ ✨  <b>Benefits</b>\n"
        "▎   ├ 🚻 Gender Filter\n"
        "▎   ├ ⚡ Priority Matching\n"
        "▎   ├ 👑 VIP Badge\n"
        "▎   ├ 🔄 Unlimited Next\n"
        "▎   ├ 🚫 Block Users\n"
        "▎   ├ 🎙️ Voice Rooms\n"
        "▎   └ 🛡️ Higher Limits\n"
        "▎\n"
        "▎ 👑  <b>Plans</b>\n"
        "▎   ├ 🚀 Sprint  · 14D· ₹99  · $1.99\n"
        "▎   ├ 🥇 Gold    · 1M · ₹179 · $3.49\n"
        "▎   ├ 💎 Diamond · 3M · ₹449 · $8.49\n"
        "▎   └ 🔥 Master  · 6M · ₹799 · $14.99\n"
        "▎\n"
        "💡 <i>Tap a plan below to pay with Stars!</i>"
    )

    inquiry = (
        "Hello! I am interested in purchasing a SparkTalks VIP membership.\n\n"
        "Please share payment options (UPI / USD) and guide me. Thank you!"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Sprint · 60⭐", callback_data="BUY_STARS_PLAN_14D", style="primary"),
         InlineKeyboardButton("🥇 Gold · 110⭐", callback_data="BUY_STARS_PLAN_1M", style="primary")],
        [InlineKeyboardButton("💎 Diamond · 250⭐", callback_data="BUY_STARS_PLAN_3M", style="primary"),
         InlineKeyboardButton("🔥 Master · 450⭐", callback_data="BUY_STARS_PLAN_6M", style="primary")],
        [InlineKeyboardButton("💬 Contact Owner (UPI / USD)", url=get_owner_link(inquiry), style="success")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])
    return text, kb


def get_profile_text(u: dict) -> str:
    name = u.get("name") or "User"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost"

    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y") if exp else "Active"
        tier = u.get("vip_tier_name") or "VIP"
        verified = " ✅" if u.get("verified") else ""
        status = f"👑 {tier}{verified}"
        exp_line = f"▎   ├ ⌛ Expires : {exp_str}\n"
    else:
        status = "⚪ Free Member"
        exp_line = ""

    if u.get("partner"):
        state_line = "🟢 Live Connected"
    elif u.get("state") == "SEARCHING":
        state_line = "🟡 Searching"
    else:
        state_line = "⚪ Idle"

    return (
        "👤  ✨  <b>Your Profile</b>  ✨  👤\n"
        "▎\n"
        f"▎ 👤 <b>{name}</b>  ·  {status}\n"
        "▎\n"
        "▎ 🆔  <b>Identity</b>\n"
        f"▎   ├ 🚻 Gender : {u.get('gender') or 'Not set'}\n"
        f"▎   ├ 🎂 Age : {age}\n"
        f"▎   ├ 🌍 Region : {country}\n"
        f"{exp_line}"
        "▎\n"
        "▎ 📝  <b>About</b>\n"
        f"▎   ├ 📝 Bio : {bio}\n"
        f"▎   ├ 🏷️ Tags : {interests}\n"
        f"▎   └ 🛡️ Mode : {visibility}\n"
        "▎\n"
        "▎ 📊  <b>Statistics</b>\n"
        f"▎   ├ 💬 Chats : {u.get('total_chats', 0)}\n"
        f"▎   └ 🏆 Matches : {u.get('total_matches', 0)}\n"
        "▎\n"
        f"🟢 <i>Status: {state_line}</i>"
    )


def get_settings_text(u: dict) -> str:
    media = "🛡️ Ask Confirmation" if u.get("confirm_media", True) else "⚡ Auto-Receive"
    pref = u.get("pref_gender", "Any")
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"
    tz_offset = u.get("timezone_offset", 5.5)
    tz_label = next((k for k, v in TIMEZONE_OPTIONS.items() if v == tz_offset), f"UTC+{tz_offset}")
    lang = (u.get("language") or "en").upper()

    return (
        "⚙️  ✨  <b>Settings</b>  ✨  ⚙️\n"
        "▎\n"
        "▎ 🔒  <b>Security & Matching</b>\n"
        f"▎   ├ 🛡️ Media : {media}\n"
        f"▎   └ 🚻 Match : {pref}\n"
        "▎\n"
        "▎ 👤  <b>Your Profile</b>\n"
        f"▎   ├ 👁️ Mode : {visibility}\n"
        f"▎   ├ 🎂 Age : {age}\n"
        f"▎   ├ 🌍 Region : {country}\n"
        f"▎   ├ 📝 Bio : {bio}\n"
        f"▎   └ 🏷️ Tags : {interests}\n"
        "▎\n"
        "▎ 🌐  <b>Preferences</b>\n"
        f"▎   ├ 🌍 Language : {lang}\n"
        f"▎   └ 🕐 Timezone : {tz_label}\n"
        "▎\n"
        "💡 <i>Tap options below to customize!</i>"
    )


def get_settings_main_kb(u: dict):
    media_btn = "🛡️ Media: ON" if u.get("confirm_media", True) else "⚡ Media: Direct"
    privacy_btn = "🔒 Ghost: ON" if not u.get("profile_public") else "👁️ Ghost: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(media_btn, callback_data="TOGGLE_SET_MEDIA", style="success"),
         InlineKeyboardButton("🚻 Match Filter", callback_data="MENU_GENDER_PREF", style="primary")],
        [InlineKeyboardButton(privacy_btn, callback_data="TOGGLE_SET_PRIVACY", style="success")],
        [InlineKeyboardButton("🎂 Age", callback_data="MENU_AGE"),
         InlineKeyboardButton("🌍 Region", callback_data="MENU_COUNTRY")],
        [InlineKeyboardButton("📝 Bio", callback_data="EDIT_BIO"),
         InlineKeyboardButton("🏷️ Interests", callback_data="MENU_INTERESTS")],
        [InlineKeyboardButton("🌐 Language", callback_data="MENU_LANGUAGE"),
         InlineKeyboardButton("🕐 Timezone", callback_data="MENU_TIMEZONE")],
        [InlineKeyboardButton("🎨 Chat Theme", callback_data="MENU_THEME")],
        [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE", style="primary")],
        [InlineKeyboardButton("🏠 Dashboard", callback_data="BACK_DASHBOARD")],
    ])


def get_gender_pref_kb(u: dict):
    pref = u.get("pref_gender", "Any")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Males Only" if pref == "Male" else "👨🏻 Males Only",
                              callback_data="SET_PREF_Male", style="primary"),
         InlineKeyboardButton("✅ Females Only" if pref == "Female" else "👩🏻 Females Only",
                              callback_data="SET_PREF_Female", style="primary")],
        [InlineKeyboardButton("✅ Anyone" if pref == "Any" else "🌐 Anyone",
                              callback_data="SET_PREF_Any", style="success")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_age_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("18 - 21", callback_data="SET_AGE_18-21", style="primary"),
         InlineKeyboardButton("22 - 25", callback_data="SET_AGE_22-25", style="primary")],
        [InlineKeyboardButton("26 - 30", callback_data="SET_AGE_26-30", style="primary"),
         InlineKeyboardButton("31+", callback_data="SET_AGE_31+", style="primary")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_country_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 India", callback_data="SET_CN_🇮🇳 India", style="primary"),
         InlineKeyboardButton("🇵🇰 Pakistan", callback_data="SET_CN_🇵🇰 Pakistan", style="primary")],
        [InlineKeyboardButton("🇺🇸 USA", callback_data="SET_CN_🇺🇸 USA", style="primary"),
         InlineKeyboardButton("🇬🇧 UK", callback_data="SET_CN_🇬🇧 UK", style="primary")],
        [InlineKeyboardButton("🇨🇦 Canada", callback_data="SET_CN_🇨🇦 Canada", style="primary"),
         InlineKeyboardButton("🇦🇪 UAE/Gulf", callback_data="SET_CN_🇦🇪 UAE/Gulf", style="primary")],
        [InlineKeyboardButton("🇳🇵 Nepal", callback_data="SET_CN_🇳🇵 Nepal", style="primary"),
         InlineKeyboardButton("🌐 Global", callback_data="SET_CN_🌐 Global", style="primary")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_interests_kb(u: dict):
    selected = u.get("interests", [])
    buttons, row = [], []
    for idx, item in enumerate(AVAILABLE_INTERESTS):
        if item in selected:
            row.append(InlineKeyboardButton(f"✅ {item}", callback_data=f"TOGGLE_INT_{idx}",
                                            style="success"))
        else:
            row.append(InlineKeyboardButton(f"➕ {item}", callback_data=f"TOGGLE_INT_{idx}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(buttons)


def get_language_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="SET_LANG_en", style="primary"),
         InlineKeyboardButton("🇮🇳 हिंदी", callback_data="SET_LANG_hi", style="primary")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="SET_LANG_ru", style="primary"),
         InlineKeyboardButton("🇸🇦 العربية", callback_data="SET_LANG_ar", style="primary")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_timezone_kb():
    rows = []
    row = []
    for label, offset in TIMEZONE_OPTIONS.items():
        row.append(InlineKeyboardButton(label, callback_data=f"SET_TZ_{offset}", style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(rows)


def get_theme_kb(current: str = None):
    themes = ["party", "cosmic", "fire", "sakura", "cute", "royal"]
    rows, row = [], []
    for t in themes:
        if t == current:
            row.append(InlineKeyboardButton(f"✅ {t.title()}", callback_data=f"THEME_{t}",
                                            style="success"))
        else:
            row.append(InlineKeyboardButton(t.title(), callback_data=f"THEME_{t}",
                                            style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🎲 Random", callback_data="THEME_random", style="primary")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(rows)