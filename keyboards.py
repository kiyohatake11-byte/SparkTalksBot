from telegram import (
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
import html as _html

from config import (
    BTN_FIND, BTN_PROFILE, BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK,
    BTN_VOICE, BTN_VOICE_LOCKED,
    BTN_SETTINGS, BTN_VIP,
    BTN_SEARCH_GENDER, BTN_FREE_VIP,
    AVAILABLE_INTERESTS, TIMEZONE_OPTIONS,
)
from utils import get_owner_link, kb, ikb, esc


# ══════════════════════════════════════════════════════════════
# REPLY KEYBOARDS
# ══════════════════════════════════════════════════════════════

def get_main_keyboard():
    """Main menu (not in chat)."""
    return ReplyKeyboardMarkup(
        [
            [kb(BTN_FIND, style="primary")],
            [kb(BTN_SEARCH_GENDER)],
            [
                kb(BTN_FREE_VIP, style="success"),
                kb(BTN_VIP, style="success"),
            ],
        ],
        resize_keyboard=True,
    )


def get_chat_keyboard(u: dict = None):
    """In-chat keyboard. VIP gets Block + active Voice."""
    is_vip = u.get("is_vip", False) if u else False
    voice_btn = BTN_VOICE if is_vip else BTN_VOICE_LOCKED

    if is_vip:
        return ReplyKeyboardMarkup(
            [
                [kb(BTN_NEXT, style="primary"),
                 kb(BTN_END, style="danger")],
                [kb(voice_btn, style="success"),
                 kb(BTN_REPORT, style="danger")],
                [kb(BTN_SEARCH_GENDER),
                 kb(BTN_BLOCK, style="danger")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        [
            [kb(BTN_NEXT, style="primary"),
             kb(BTN_END, style="danger")],
            [kb(voice_btn),
             kb(BTN_REPORT, style="danger")],
            [kb(BTN_SEARCH_GENDER)],
        ],
        resize_keyboard=True,
    )


# ══════════════════════════════════════════════════════════════
# INLINE KEYBOARDS
# ══════════════════════════════════════════════════════════════

def get_store_markup(u: dict = None):
    is_vip = u.get("is_vip", False) if u else False
    tier = (u.get("vip_tier_name") or "VIP Active") if u else "VIP Active"
    status = f"👑 {esc(tier)}" if is_vip else "⚪ Free Member"

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
        "▎   ├ 🔄 Reconnect\n"
        "▎   ├ 💾 Save Partner\n"
        "▎   └ 🛡️ Higher Limits\n"
        "▎\n"
        "▎ 👑  <b>Plans</b>\n"
        "▎   ├ 🚀 Sprint  · 14D · ₹149  · $2.79\n"
        "▎   ├ 🥇 Gold    · 1M  · ₹249  · $4.89\n"
        "▎   ├ 💎 Diamond · 3M  · ₹649  · $11.89\n"
        "▎   └ 🔥 Master  · 6M  · ₹1149 · $20.99\n"
        "▎\n"
        "💡 <i>Tap a plan below to pay with Stars!</i>"
    )

    inquiry = (
        "Hello! I am interested in purchasing a SparkTalks VIP membership.\n\n"
        "Please share payment options (UPI / USD) and guide me. Thank you!"
    )

    kb_markup = InlineKeyboardMarkup([
        [ikb("🚀 Sprint · 99⭐", callback_data="BUY_STARS_PLAN_14D", style="primary"),
         ikb("🥇 Gold · 180⭐", callback_data="BUY_STARS_PLAN_1M", style="primary")],
        [ikb("💎 Diamond · 415⭐", callback_data="BUY_STARS_PLAN_3M", style="primary"),
         ikb("🔥 Master · 745⭐", callback_data="BUY_STARS_PLAN_6M", style="primary")],
        [ikb("💬 Contact Owner (UPI / USD)", url=get_owner_link(inquiry), style="success")],
        [ikb("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])
    return text, kb_markup


def get_profile_text(u: dict) -> str:
    name = esc(u.get("name") or "User")
    age = u.get("age") or "Unspecified"
    country = esc(u.get("country") or "Unspecified")
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(esc(i) for i in (u.get("interests", []) or [])) or "None"
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost"

    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y") if exp else "Active"
        tier = esc(u.get("vip_tier_name") or "VIP")
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
    country = esc(u.get("country") or "Unspecified")
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(esc(i) for i in (u.get("interests", []) or [])) or "None"
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
        [ikb(media_btn, callback_data="TOGGLE_SET_MEDIA", style="success"),
         ikb("🚻 Match Filter", callback_data="MENU_GENDER_PREF", style="primary")],
        [ikb(privacy_btn, callback_data="TOGGLE_SET_PRIVACY", style="success")],
        [ikb("🎂 Age", callback_data="MENU_AGE"),
         ikb("🌍 Region", callback_data="MENU_COUNTRY")],
        [ikb("📝 Bio", callback_data="EDIT_BIO"),
         ikb("🏷️ Interests", callback_data="MENU_INTERESTS")],
        [ikb("🌐 Language", callback_data="MENU_LANGUAGE"),
         ikb("🕐 Timezone", callback_data="MENU_TIMEZONE")],
        [ikb("🎨 Chat Theme", callback_data="MENU_THEME")],
        [ikb("🛍️ Get VIP", callback_data="BUY_STORE", style="success")],
        [ikb("🏠 Dashboard", callback_data="BACK_DASHBOARD")],
    ])


def get_gender_pref_kb(u: dict):
    pref = u.get("pref_gender", "Any")
    return InlineKeyboardMarkup([
        [ikb("✅ Males Only" if pref == "Male" else "👨🏻 Males Only",
             callback_data="SET_PREF_Male", style="primary"),
         ikb("✅ Females Only" if pref == "Female" else "👩🏻 Females Only",
             callback_data="SET_PREF_Female", style="primary")],
        [ikb("✅ Anyone" if pref == "Any" else "🌐 Anyone",
             callback_data="SET_PREF_Any", style="success")],
        [ikb("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_age_kb():
    return InlineKeyboardMarkup([
        [ikb("18 - 21", callback_data="SET_AGE_18-21", style="primary"),
         ikb("22 - 25", callback_data="SET_AGE_22-25", style="primary")],
        [ikb("26 - 30", callback_data="SET_AGE_26-30", style="primary"),
         ikb("31+", callback_data="SET_AGE_31+", style="primary")],
        [ikb("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_country_kb():
    return InlineKeyboardMarkup([
        [ikb("🇮🇳 India", callback_data="SET_CN_🇮🇳 India", style="primary"),
         ikb("🇵🇰 Pakistan", callback_data="SET_CN_🇵🇰 Pakistan", style="primary")],
        [ikb("🇺🇸 USA", callback_data="SET_CN_🇺🇸 USA", style="primary"),
         ikb("🇬🇧 UK", callback_data="SET_CN_🇬🇧 UK", style="primary")],
        [ikb("🇨🇦 Canada", callback_data="SET_CN_🇨🇦 Canada", style="primary"),
         ikb("🇦🇪 UAE/Gulf", callback_data="SET_CN_🇦🇪 UAE/Gulf", style="primary")],
        [ikb("🇳🇵 Nepal", callback_data="SET_CN_🇳🇵 Nepal", style="primary"),
         ikb("🌐 Global", callback_data="SET_CN_🌐 Global", style="primary")],
        [ikb("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_interests_kb(u: dict):
    selected = u.get("interests", [])
    buttons, row = [], []
    for idx, item in enumerate(AVAILABLE_INTERESTS):
        if item in selected:
            row.append(ikb(f"✅ {item}", callback_data=f"TOGGLE_INT_{idx}",
                           style="success"))
        else:
            row.append(ikb(f"➕ {item}", callback_data=f"TOGGLE_INT_{idx}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([ikb("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(buttons)


def get_language_kb():
    return InlineKeyboardMarkup([
        [ikb("🇬🇧 English", callback_data="SET_LANG_en", style="primary"),
         ikb("🇮🇳 हिंदी", callback_data="SET_LANG_hi", style="primary")],
        [ikb("🇷🇺 Русский", callback_data="SET_LANG_ru", style="primary"),
         ikb("🇸🇦 العربية", callback_data="SET_LANG_ar", style="primary")],
        [ikb("◀️ Back", callback_data="OPEN_SETTINGS")],
    ])


def get_timezone_kb():
    rows = []
    row = []
    for label, offset in TIMEZONE_OPTIONS.items():
        row.append(ikb(label, callback_data=f"SET_TZ_{offset}", style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ikb("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(rows)


def get_theme_kb(current: str = None):
    themes = ["party", "cosmic", "fire", "sakura", "cute", "royal"]
    rows, row = [], []
    for t in themes:
        if t == current:
            row.append(ikb(f"✅ {t.title()}", callback_data=f"THEME_{t}",
                           style="success"))
        else:
            row.append(ikb(t.title(), callback_data=f"THEME_{t}", style="primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([ikb("🎲 Random", callback_data="THEME_random", style="primary")])
    rows.append([ikb("◀️ Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(rows)