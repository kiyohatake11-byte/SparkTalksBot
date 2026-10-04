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
    tier = (u.get("vip_tier_name") or "VIP Active") if u else "VIP Active"
    status_str = f"\U0001F451 {tier}" if is_vip else "\u26AA Free Member"

    blocks = [
        {"type": "line", "content": f"\u26A1 {to_bold(f'Status : {status_str}')}"},
        {"type": "divider"},
        {"type": "section", "emoji": "\u2728", "heading": "VIP Benefits"},
        {"type": "line", "content": "\u2022 \U0001F6BB Gender Filter"},
        {"type": "line", "content": "\u2022 \u26A1 Priority Matching"},
        {"type": "line", "content": "\u2022 \U0001F451 Exclusive VIP Badge"},
        {"type": "line", "content": "\u2022 \U0001F504 Unlimited Next"},
        {"type": "line", "content": "\u2022 \U0001F6AB Block Unwanted Users"},
        {"type": "line", "content": "\u2022 \U0001F6E1\uFE0F Higher limits"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F451", "heading": "Available Plans"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"\U0001F680 {to_bold('Sprint')} \u00B7 14 Days"},
        {"type": "line", "content": "   \u20B999  \u00B7  $1.99  \u00B7  60\u2B50"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"\U0001F947 {to_bold('Gold')} \u00B7 1 Month  \U0001F525 Popular"},
        {"type": "line", "content": "   \u20B9179  \u00B7  $3.49  \u00B7  110\u2B50"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"\U0001F48E {to_bold('Diamond')} \u00B7 3 Months"},
        {"type": "line", "content": "   \u20B9449  \u00B7  $8.49  \u00B7  250\u2B50"},
        {"type": "text", "content": ""},
        {"type": "line", "content": f"\U0001F525 {to_bold('Master')} \u00B7 6 Months"},
        {"type": "line", "content": "   \u20B9799  \u00B7  $14.99  \u00B7  450\u2B50"},
        {"type": "divider"},
        {"type": "text", "content": "\U0001F4A1 Tap a plan below to pay with Telegram Stars."},
    ]

    inquiry = (
        "Hello! I am interested in purchasing a SparkTalks VIP membership.\n\n"
        "Please share the available payment options (UPI / USD / Other) "
        "and guide me on how to complete the purchase. Thank you!"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F680 Sprint \u00B7 60\u2B50", callback_data="BUY_STARS_PLAN_14D"),
         InlineKeyboardButton("\U0001F947 Gold \u00B7 110\u2B50", callback_data="BUY_STARS_PLAN_1M")],
        [InlineKeyboardButton("\U0001F48E Diamond \u00B7 250\u2B50", callback_data="BUY_STARS_PLAN_3M"),
         InlineKeyboardButton("\U0001F525 Master \u00B7 450\u2B50", callback_data="BUY_STARS_PLAN_6M")],
        [InlineKeyboardButton("\U0001F4AC Contact Owner (UPI / USD)", url=get_owner_link(inquiry))],
        [InlineKeyboardButton("\u25C0\uFE0F Back", callback_data="OPEN_SETTINGS")],
    ])
    return box_card("VIP Store", blocks, emoji="\U0001F6CD\uFE0F"), kb


def get_profile_text(u: dict) -> str:
    import html as _html
    status = "\U0001F7E2 Live Connected" if u.get("partner") else (
        "\U0001F7E1 Searching" if u.get("state") == "SEARCHING" else "\u26AA Idle"
    )
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio set.")
    interests = ", ".join(u.get("interests", [])) or "None"
    visibility = "\U0001F441\uFE0F Public" if u.get("profile_public") else "\U0001F512 Ghost Mode"

    if u.get("is_vip"):
        exp = u.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b %Y") if exp else "Active"
        tier = u.get("vip_tier_name") or "VIP"
        vip = f"\U0001F451 {tier} ({exp_str})"
    else:
        vip = "\u26AA Free Member"

    blocks = [
        {"type": "section", "emoji": "\U0001F194", "heading": "Identity"},
        {"type": "line", "content": f"\U0001F464 Gender: {u.get('gender') or 'Not set'}"},
        {"type": "line", "content": f"\U0001F382 Age: {age}"},
        {"type": "line", "content": f"\U0001F30D Region: {country}"},
        {"type": "line", "content": f"\u26A1 Status: {vip}"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F4DD", "heading": "About"},
        {"type": "line", "content": f"\U0001F4DD Bio: {bio}"},
        {"type": "line", "content": f"\U0001F3F7\uFE0F Interests: {interests}"},
        {"type": "line", "content": f"\U0001F6E1\uFE0F Privacy: {visibility}"},
        {"type": "line", "content": f"\U0001F7E2 State: {status}"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F4CA", "heading": "Statistics"},
        {"type": "line", "content": f"\u2022 Total Chats: {u.get('total_chats', 0)}"},
        {"type": "line", "content": f"\u2022 Total Matches: {u.get('total_matches', 0)}"},
    ]
    return box_card("Your Profile", blocks, emoji="\U0001F464")


def get_settings_text(u: dict) -> str:
    import html as _html
    media = "\U0001F6E1\uFE0F Ask Confirmation" if u.get("confirm_media", True) else "\u26A1 Auto-Receive"
    pref = u.get("pref_gender", "Any")
    visibility = "\U0001F441\uFE0F Public" if u.get("profile_public") else "\U0001F512 Ghost Mode"
    age = u.get("age") or "Unspecified"
    country = u.get("country") or "Unspecified"
    bio = _html.escape(u.get("bio") or "No bio.")
    interests = ", ".join(u.get("interests", [])) or "None"

    blocks = [
        {"type": "section", "emoji": "\U0001F512", "heading": "Security & Matching"},
        {"type": "line", "content": f"\u2022 Media: {media}"},
        {"type": "line", "content": f"\u2022 Match: {pref}"},
        {"type": "divider"},
        {"type": "section", "emoji": "\U0001F464", "heading": "Your Profile"},
        {"type": "line", "content": f"\u2022 Mode: {visibility}"},
        {"type": "line", "content": f"\u2022 Age: {age}"},
        {"type": "line", "content": f"\u2022 Region: {country}"},
        {"type": "line", "content": f"\u2022 Bio: {bio}"},
        {"type": "line", "content": f"\u2022 Tags: {interests}"},
    ]
    return box_card("Settings", blocks, emoji="\u2699\uFE0F")


def get_settings_main_kb(u: dict):
    media_btn = "\U0001F6E1\uFE0F Media: ON" if u.get("confirm_media", True) else "\u26A1 Media: Direct"
    privacy_btn = "\U0001F512 Ghost: ON" if not u.get("profile_public") else "\U0001F441\uFE0F Ghost: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(media_btn, callback_data="TOGGLE_SET_MEDIA"),
         InlineKeyboardButton("\U0001F6BB Match Filter", callback_data="MENU_GENDER_PREF")],
        [InlineKeyboardButton(privacy_btn, callback_data="TOGGLE_SET_PRIVACY")],
        [InlineKeyboardButton("\U0001F382 Age", callback_data="MENU_AGE"),
         InlineKeyboardButton("\U0001F30D Region", callback_data="MENU_COUNTRY")],
        [InlineKeyboardButton("\U0001F4DD Bio", callback_data="EDIT_BIO"),
         InlineKeyboardButton("\U0001F3F7\uFE0F Interests", callback_data="MENU_INTERESTS")],
        [InlineKeyboardButton("\U0001F6CD\uFE0F Get VIP", callback_data="BUY_STORE")],
        [InlineKeyboardButton("\U0001F3E0 Dashboard", callback_data="BACK_DASHBOARD")],
    ])


def get_gender_pref_kb(u: dict):
    pref = u.get("pref_gender", "Any")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2705 Males Only" if pref == "Male" else "\U0001F468\u200D\U0001F9B1 Males Only",
                              callback_data="SET_PREF_Male"),
         InlineKeyboardButton("\u2705 Females Only" if pref == "Female" else "\U0001F469\u200D\U0001F9B1 Females Only",
                              callback_data="SET_PREF_Female")],
        [InlineKeyboardButton("\u2705 Anyone" if pref == "Any" else "\U0001F310 Anyone",
                              callback_data="SET_PREF_Any")],
        [InlineKeyboardButton("\u25C0\uFE0F Back", callback_data="OPEN_SETTINGS")],
    ])


def get_age_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("18 - 21", callback_data="SET_AGE_18-21"),
         InlineKeyboardButton("22 - 25", callback_data="SET_AGE_22-25")],
        [InlineKeyboardButton("26 - 30", callback_data="SET_AGE_26-30"),
         InlineKeyboardButton("31+", callback_data="SET_AGE_31+")],
        [InlineKeyboardButton("\u25C0\uFE0F Back", callback_data="OPEN_SETTINGS")],
    ])


def get_country_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F1EE\U0001F1F3 India", callback_data="SET_CN_\U0001F1EE\U0001F1F3 India"),
         InlineKeyboardButton("\U0001F1F5\U0001F1F0 Pakistan", callback_data="SET_CN_\U0001F1F5\U0001F1F0 Pakistan")],
        [InlineKeyboardButton("\U0001F1FA\U0001F1F8 USA", callback_data="SET_CN_\U0001F1FA\U0001F1F8 USA"),
         InlineKeyboardButton("\U0001F1EC\U0001F1E7 UK", callback_data="SET_CN_\U0001F1EC\U0001F1E7 UK")],
        [InlineKeyboardButton("\U0001F1E8\U0001F1E6 Canada", callback_data="SET_CN_\U0001F1E8\U0001F1E6 Canada"),
         InlineKeyboardButton("\U0001F1E6\U0001F1EA UAE/Gulf", callback_data="SET_CN_\U0001F1E6\U0001F1EA UAE/Gulf")],
        [InlineKeyboardButton("\U0001F1F3\U0001F1F5 Nepal", callback_data="SET_CN_\U0001F1F3\U0001F1F5 Nepal"),
         InlineKeyboardButton("\U0001F310 Global", callback_data="SET_CN_\U0001F310 Global")],
        [InlineKeyboardButton("\u25C0\uFE0F Back", callback_data="OPEN_SETTINGS")],
    ])


def get_interests_kb(u: dict):
    selected = u.get("interests", [])
    buttons, row = [], []
    for idx, item in enumerate(AVAILABLE_INTERESTS):
        prefix = "\u2705 " if item in selected else "\u2795 "
        row.append(InlineKeyboardButton(f"{prefix}{item}", callback_data=f"TOGGLE_INT_{idx}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("\u25C0\uFE0F Back", callback_data="OPEN_SETTINGS")])
    return InlineKeyboardMarkup(buttons)
