from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)

from config import (
    BTN_FIND, BTN_PROFILE, BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK,
    AVAILABLE_INTERESTS,
)
from utils import get_owner_link, to_bold


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_FIND)],
         [KeyboardButton(BTN_PROFILE)]],
        resize_keyboard=True,
    )


def get_chat_keyboard(u: dict = None):
    is_vip = u.get("is_vip", False) if u else False
    if is_vip:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(BTN_NEXT), KeyboardButton(BTN_END)],
             [KeyboardButton(BTN_REPORT), KeyboardButton(BTN_BLOCK)]],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_NEXT), KeyboardButton(BTN_END)],
         [KeyboardButton(BTN_REPORT)]],
        resize_keyboard=True,
    )


# ══════════════════════════════════════════════════════════════
# 🛍️ VIP STORE — Sidebar Style
# ══════════════════════════════════════════════════════════════

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
        "▎   └ 🛡️ Higher Limits\n"
        "▎\n"
        "▎ 👑  <b>Plans</b>\n"
        "▎   ├ 🚀 Sprint · 14d · ₹99\n"
        "▎   ├ 🥇 Gold · 1m · ₹179\n"
        "▎   ├ 💎 Diamond · 3m · ₹449\n"
        "▎   └ 🔥 Master · 6m · ₹799\n"
        "▎\n"
        "💡 <i>Tap a plan below to pay with Stars!</i>"
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
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])
    return text, kb


# ══════════════════════════════════════════════════════════════
# 👤 PROFILE — Sidebar Style
# ══════════════════════════════════════════════════════════════

def get_profile_text(u: dict) -> str:
    import html as _html

    name = u.get("name") or "User"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost"

    # Status
    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y") if exp else "Active"
        tier = u.get("vip_tier_name") or "VIP"
        status = f"👑 {tier}"
        exp_line = f"▎   ├ ⌛ Expires : {exp_str}\n"
    else:
        status = "⚪ Free Member"
        exp_line = ""

    # Live state
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


# ══════════════════════════════════════════════════════════════
# ⚙️ SETTINGS — Sidebar Style
# ══════════════════════════════════════════════════════════════

def get_settings_text(u: dict) -> str:
    import html as _html

    media = "🛡️ Ask Confirmation" if u.get("confirm_media", True) else "⚡ Auto-Receive"
    pref = u.get("pref_gender", "Any")
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"

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
        "💡 <i>Tap options below to customize!</i>"
    )


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
        [InlineKeyboardButton("🛍️ Get VIP", callback_data="BUY_STORE")],
        [InlineKeyboardButton("🏠 Dashboard", callback_data="BACK_DASHBOARD")],
    ])


def get_gender_pref_kb(u: dict):
    pref = u.get("pref_gender", "Any")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Males Only" if pref == "Male" else "👨🏻 Males Only",
                              callback_data="SET_PREF_Male"),
         InlineKeyboardButton("✅ Females Only" if pref == "Female" else "👩🏻 Females Only",
                              callback_data="SET_PREF_Female")],
        [InlineKeyboardButton("✅ Anyone" if pref == "Any" else "🌐 Anyone",
                              callback_data="SET_PREF_Any")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_age_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("18 - 21", callback_data="SET_AGE_18-21"),
         InlineKeyboardButton("22 - 25", callback_data="SET_AGE_22-25")],
        [InlineKeyboardButton("26 - 30", callback_data="SET_AGE_26-30"),
         InlineKeyboardButton("31+", callback_data="SET_AGE_31+")],
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
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
        [InlineKeyboardButton("◀️ Back", callback_data="OPEN_SETTINGS")],
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