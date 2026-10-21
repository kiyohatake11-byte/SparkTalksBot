import os
import secrets as _secrets
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sparktalks")


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        logger.warning(f"Invalid int for {key}={raw!r} — using default {default}")
        return default


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = _env_int("PORT", 8080)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = _env_int("OWNER_ID", 0)
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
ADMIN_IDS = [
    int(i.strip())
    for i in os.getenv("ADMIN_IDS", "").split(",")
    if i.strip().isdigit()
]
BOT_USERNAME = os.getenv("BOT_USERNAME", "SparkTalksBot")

# ⭐ NEW: Webhook config (40K+ users zero-gap)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

if WEBHOOK_URL and not WEBHOOK_SECRET:
    WEBHOOK_SECRET = _secrets.token_urlsafe(32)
    logger.warning(
        "⚠️ WEBHOOK_SECRET not set — generated random. "
        "Set it in env for consistency."
    )

WELCOME_IMAGE_FILE_ID = os.getenv("WELCOME_IMAGE_FILE_ID", "").strip()
DASHBOARD_IMAGE_FILE_ID = os.getenv("DASHBOARD_IMAGE_FILE_ID", "").strip()

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY")

if not DASHBOARD_PASS:
    DASHBOARD_PASS = _secrets.token_urlsafe(16)
    logger.warning(
        "⚠️ DASHBOARD_PASS not set — generated a random password. "
        "Set DASHBOARD_PASS in .env for a stable login."
    )
if not DASHBOARD_SECRET_KEY:
    DASHBOARD_SECRET_KEY = _secrets.token_hex(32)
    logger.warning("⚠️ DASHBOARD_SECRET_KEY not set — using random.")

VOICE_ROOM_EXPIRE_SECONDS = _env_int("VOICE_ROOM_EXPIRE_SECONDS", 2700)
VOICE_ROOM_BASE_URL = os.getenv("VOICE_ROOM_BASE_URL", "").strip()
VOICE_ROOM_MIN_VIP = True

MAX_REACTION_ENTRIES = 5000
MAX_BLOCKED_USERS = 100
NEXT_COOLDOWN_SECONDS = 2
QUEUE_CLEANUP_INTERVAL = 300
VIP_CHECK_INTERVAL = 3600
INACTIVE_USER_TIMEOUT = 3600
CLEANUP_USERS_INTERVAL = 600
MAX_PENDING_MEDIA = 20

BROADCAST_WORKERS = 35
BROADCAST_PROGRESS_EVERY = 500
BROADCAST_RETRY_FACTOR = 1.2

BROADCAST_PARALLEL = 35
BROADCAST_BATCH_DELAY = 0.0
BROADCAST_SLEEP_SECONDS = 0.1

ALLOW_INSTANT_REMATCH = True
MAX_RECENT_PARTNERS = 10
BLOCK_REQUIRES_VIP = True

ANTI_LINK_ENABLED = True
ANTI_LINK_WARN_LIMIT = 3
FLOOD_MSG_LIMIT = 12
FLOOD_WINDOW_SECONDS = 10
FLOOD_MUTE_SECONDS = 300

REFERRAL_REWARDS_NEEDED = 20
REFERRAL_REWARD_DAYS = 1

REFERRAL_MILESTONES = {
    3:  {"type": "gender_credits", "amount": 1, "label": "🎯 1× Gender Filter"},
    5:  {"type": "gender_credits", "amount": 2, "label": "🎯 2× Gender Filter"},
    10: {"type": "vip_hours",      "amount": 1, "label": "👑 1 Hour VIP"},
    20: {"type": "vip_days",       "amount": 1, "label": "👑 1 Day VIP"},
}

VIP_PRIORITY_QUEUE = True
SYNC_MESSAGE_EDITS = True

IMAGE_HASH_ENABLED = True
IMAGE_HASH_THRESHOLD = 6
IMAGE_HASH_MAX_SEEN = 5000
IMAGE_HASH_AUTO_MUTE_AFTER = 3
IMAGE_HASH_CLEANUP_MAX_AGE = 3600

DUPLICATE_TEXT_THRESHOLD = 4
DUPLICATE_TEXT_WINDOW = 300
DUPLICATE_TEXT_MAX_STORE = 5000

MEDIA_TIMEOUT_SECONDS = 120

RECONNECT_DAILY_LIMIT = 5
MAX_SAVED_PARTNERS = 20
SAVE_PARTNER_NOTIFY = True

RECONNECT_BUSY_MSG = (
    "🕐  <b>Partner is busy right now</b>\n\n"
    "💡 <i>Please try again in a few minutes.</i>"
)

SAVE_PARTNER_NOTIFY_PARTNER = False
SAVE_REQUEST_TOKEN_TTL = 300

SCHEDULED_BROADCAST_CHECK_INTERVAL = 30

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ["en", "hi", "ru", "ar"]

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

BTN_FIND          = "❤️ ꜰɪɴᴅ ᴘᴀʀᴛɴᴇʀ"
BTN_SEARCH_GENDER = "🚻 ꜱᴇᴀʀᴄʜ ʙʏ ɢᴇɴᴅᴇʀ"
BTN_FREE_VIP      = "🎁 ꜰʀᴇᴇ ᴠɪᴘ"
BTN_PROFILE       = "👤 ᴘʀᴏꜰɪʟᴇ"
BTN_NEXT          = "🔄 ɴᴇxᴛ"
BTN_END           = "🛑 ᴇɴᴅ ᴄʜᴀᴛ"
BTN_REPORT        = "🚨 ʀᴇᴘᴏʀᴛ"
BTN_BLOCK         = "🚫 ʙʟᴏᴄᴋ"
BTN_VOICE         = "🎙️ ᴠᴏɪᴄᴇ ᴄᴀʟʟ"
BTN_VOICE_LOCKED  = "🎙️ ᴠᴏɪᴄᴇ 👑"
BTN_SETTINGS      = "⚙️ ꜱᴇᴛᴛɪɴɢꜱ"
BTN_VIP           = "🛍️ ɢᴇᴛ ᴠɪᴘ"

COMMUNITY_CHANNEL_USERNAME = os.getenv("COMMUNITY_CHANNEL_USERNAME", "").replace("@", "").strip()
COMMUNITY_CHANNEL_LINK = os.getenv("COMMUNITY_CHANNEL_LINK", "").strip()
COMMUNITY_CHANNEL_ID = os.getenv("COMMUNITY_CHANNEL_ID", "").strip()
FORCE_JOIN_AFTER_MATCHES = _env_int("FORCE_JOIN_AFTER_MATCHES", 2)