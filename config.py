import os
import secrets as _secrets
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sparktalks")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()]
BOT_USERNAME = os.getenv("BOT_USERNAME", "SparkTalksBot")

# Web dashboard auth
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY")

# ⚠️ SECURITY: Generate random passwords if not set (better than "admin")
if not DASHBOARD_PASS:
    DASHBOARD_PASS = _secrets.token_urlsafe(16)
    logger.warning(
        "⚠️ DASHBOARD_PASS not set! Generated random: %s "
        "(will change on every restart — set in .env for stability)",
        DASHBOARD_PASS,
    )
if not DASHBOARD_SECRET_KEY:
    DASHBOARD_SECRET_KEY = _secrets.token_hex(32)
    logger.warning("⚠️ DASHBOARD_SECRET_KEY not set — using random.")

# Voice rooms
VOICE_ROOM_EXPIRE_SECONDS = int(os.getenv("VOICE_ROOM_EXPIRE_SECONDS", "2700"))
VOICE_ROOM_BASE_URL = os.getenv("VOICE_ROOM_BASE_URL", "")
VOICE_ROOM_MIN_VIP = True

# Core limits
MAX_REACTION_ENTRIES = 5000
MAX_BLOCKED_USERS = 100
NEXT_COOLDOWN_SECONDS = 2
QUEUE_CLEANUP_INTERVAL = 300
VIP_CHECK_INTERVAL = 3600
INACTIVE_USER_TIMEOUT = 3600
CLEANUP_USERS_INTERVAL = 600
MAX_PENDING_MEDIA = 20
BROADCAST_SLEEP_SECONDS = 0.1

ALLOW_INSTANT_REMATCH = True
MAX_RECENT_PARTNERS = 10
BLOCK_REQUIRES_VIP = True

# Anti-link / anti-contact
ANTI_LINK_ENABLED = True
ANTI_LINK_WARN_LIMIT = 3
FLOOD_MSG_LIMIT = 12
FLOOD_WINDOW_SECONDS = 10
FLOOD_MUTE_SECONDS = 300

# Referral
REFERRAL_REWARDS_NEEDED = 20
REFERRAL_REWARD_DAYS = 1

# VIP priority
VIP_PRIORITY_QUEUE = True

# Edit sync
SYNC_MESSAGE_EDITS = True

# Image hash anti-spam
IMAGE_HASH_ENABLED = True
IMAGE_HASH_THRESHOLD = 6
IMAGE_HASH_MAX_SEEN = 5000
IMAGE_HASH_AUTO_MUTE_AFTER = 3
IMAGE_HASH_CLEANUP_MAX_AGE = 3600

# Duplicate text detection
DUPLICATE_TEXT_THRESHOLD = 4
DUPLICATE_TEXT_WINDOW = 300
DUPLICATE_TEXT_MAX_STORE = 5000

# Media timeout
MEDIA_TIMEOUT_SECONDS = 120

# Reconnect / Save partner
RECONNECT_DAILY_LIMIT = 5
MAX_SAVED_PARTNERS = 20
SAVE_PARTNER_NOTIFY = True
RECONNECT_BUSY_MSG = "🕐 Partner abhi busy hai, thodi der baad try karo"

# Scheduler
SCHEDULED_BROADCAST_CHECK_INTERVAL = 30

# i18n
DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ["en", "hi", "ru", "ar"]

# Timezones
DEFAULT_TIMEZONE_OFFSET = 5.5

AVAILABLE_INTERESTS = [
    "🎮 Gaming", "🎵 Music", "🎬 Movies",
    "💻 Tech", "⚽ Sports", "📚 Books",
    "🎨 Art", "✈️ Travel", "🍿 Anime",
]

TIMEZONE_OPTIONS = {
    "IST (India)": 5.5,
    "GST (UAE)": 4.0,
    "PKT (Pakistan)": 5.0,
    "MSK (Moscow)": 3.0,
    "UTC": 0.0,
    "EST (US East)": -5.0,
    "PST (US West)": -8.0,
    "GMT (UK)": 0.0,
    "CET (Europe)": 1.0,
    "AST (Arabic)": 3.0,
}

VIP_PLANS = {
    "PLAN_14D": {
        "days": 14, "price_inr": "₹149", "price_usd": "$2.79",
        "name": "🚀 Sprint VIP Pass", "label": "14 Days", "stars": 99,
    },
    "PLAN_1M": {
        "days": 30, "price_inr": "₹249", "price_usd": "$4.89",
        "name": "🥇 Gold VIP Pass", "label": "1 Month", "stars": 180,
    },
    "PLAN_3M": {
        "days": 90, "price_inr": "₹649", "price_usd": "$11.89",
        "name": "💎 Diamond VIP Pass", "label": "3 Months", "stars": 415,
    },
    "PLAN_6M": {
        "days": 180, "price_inr": "₹1149", "price_usd": "$20.99",
        "name": "🔥 Master VIP Pass", "label": "6 Months", "stars": 745,
    },
}

# Button labels
BTN_FIND     = "❤️ ꜰɪɴᴅ ᴘᴀʀᴛɴᴇʀ"
BTN_PROFILE  = "👤 ᴘʀᴏꜰɪʟᴇ"
BTN_NEXT     = "🔄 ɴᴇxᴛ"
BTN_END      = "🛑 ᴇɴᴅ ᴄʜᴀᴛ"
BTN_REPORT   = "🚨 ʀᴇᴘᴏʀᴛ"
BTN_BLOCK    = "🚫 ʙʟᴏᴄᴋ"
BTN_VOICE        = "🎙️ ᴠᴏɪᴄᴇ ᴄᴀʟʟ"
BTN_VOICE_LOCKED = "🎙️ ᴠᴏɪᴄᴇ 👑"
BTN_SETTINGS = "⚙️ ꜱᴇᴛᴛɪɴɢꜱ"
BTN_VIP      = "🛍️ ᴠɪᴘ ꜱᴛᴏʀᴇ"