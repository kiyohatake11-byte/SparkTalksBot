from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)

from config import (
    BTN_FIND, BTN_PROFILE, BTN_NEXT, BTN_END, BTN_REPORT, BTN_BLOCK,
    AVAILABLE_INTERESTS,
)
from utils import box_card, get_owner_link, to_bold


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_FIND)],
         [KeyboardButton(BTN_PROFILE)]],
        resize_keyboard=True,
    )


def get_chat_keyboard(u: dict = None):
    """Chat keyboard. Block button only for VIP."""
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


def get_store_markup(u: dict = None):
    is_vip = u.get("is_vip", False) if u else False
    status_str = f"👑 {u.get('vip_tier_name', 'VIP Active')}" if is_vip else "⚪ Free Member"

    blocks = [
        {"type": "line", "content": f"⚡ {to_bold(f'Status : {status_str}')}"},
        {"type": "divider"},
        {"type": "section", "emoji": "✨", "heading": "VIP Benefits"},
        {"type": "line", "content": "• 🚻 Gender Filter"},
        {"type": "line", "content": "• ⚡ Priority Matching"},
        {"type": "line", "content": "• 👑 Exclusive VIP Badge"},
        {"type": "line", "content": "• 🔄 Unlimited Next"},
        {"type": "line", "content": "• 🚫 Block Unwanted Users"},
        {"type": "line", "content": "• 🛡️ Higher limits"},
        {"type": "divider"},
        {"type": "section", "emoji": "👑", "heading": "Available Plans"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"🚀 {to_bold('Sprint')} · 14 Days"},
        {"type": "line", "content": "   ₹99  ·  $1.99  ·  60⭐"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"🥇 {to_bold('Gold')} · 1 Month  🔥 Popular"},
        {"type": "line", "content": "   ₹179  ·  $3.49  ·  110⭐"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"💎 {to_bold('Diamond')} · 3 Months"},
        {"type": "line", "content": "   ₹449  ·  $8.49  ·  250⭐"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"🔥 {to_bold('Master')} · 6 Months"},
        {"type": "line", "content": "   ₹799  ·  $14.99  ·  450⭐"},
        {"type": "divider"},
        {"type": "text", "content": "💡 Tap a plan below to pay with Telegram Stars."},
    ]

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
    return box_card("VIP Store", blocks, emoji="🛍️"), kb


def get_profile_text(u: dict) -> str:
    import html as _html
    status = "🟢 Live Connected" if u.get("partner") else (
        "🟡 Searching" if u.get("state") == "SEARCHING" else "⚪ Idle"
    )
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost Mode"

    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y") if exp else "Active"
        vip = f"👑 {u.get('vip_tier_name', 'VIP')} ({exp_str})"
    else:
        vip = "⚪ Free Member"

    blocks = [
        {"type": "section", "emoji": "🆔", "heading": "Identity"},
        {"type": "line", "content": f"👤 Gender: {u.get('gender') or 'Not set'}"},
        {"type": "line", "content": f"🎂 Age: {age}"},
        {"type": "line", "content": f"🌍 Region: {country}"},
        {"type": "line", "content": f"⚡ Status: {vip}"},
        {"type": "divider"},
        {"type": "section", "emoji": "📝", "heading": "About"},
        {"type": "line", "content": f"📝 Bio: {bio}"},
        {"type": "line", "content": f"🏷️ Interests: {interests}"},
        {"type": "line", "content": f"🛡️ Privacy: {visibility}"},
        {"type": "line", "content": f"🟢 State: {status}"},
        {"type": "divider"},
        {"type": "section", "emoji": "📊", "heading": "Statistics"},
        {"type": "line", "content": f"• Total Chats: {u.get('total_chats', 0)}"},
        {"type": "line", "content": f"• Total Matches: {u.get('total_matches', 0)}"},
    ]
    return box_card("Your Profile", blocks, emoji="👤")


def get_settings_text(u: dict) -> str:
    import html as _html
    media = "🛡️ Ask Confirmation" if u.get("confirm_media", True) else "⚡ Auto-Receive"
    pref = u.get("pref_gender", "Any")
    visibility = "👁️ Public" if u.get("profile_public") else "🔒 Ghost Mode"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio.")
    interests = ", ".join(u.get("interests", [])) or "None"

    blocks = [
        {"type": "section", "emoji": "🔒", "heading": "Security & Matching"},
        {"type": "line", "content": f"• Media: {media}"},
        {"type": "line", "content": f"• Match: {pref}"},
        {"type": "divider"},
        {"type": "section", "emoji": "👤", "heading": "Your Profile"},
        {"type": "line", "content": f"• Mode: {visibility}"},
        {"type": "line", "content": f"• Age: {age}"},
        {"type": "line", "content": f"• Region: {country}"},
        {"type": "line", "content": f"• Bio: {bio}"},
        {"type": "line", "content": f"• Tags: {interests}"},
    ]
    return box_card("Settings", blocks, emoji="⚙️")


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
        [InlineKeyboardButton("❌ Close", callback_data="CLOSE_SETTINGS")],
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