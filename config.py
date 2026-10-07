import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()]
BOT_USERNAME = os.getenv("BOT_USERNAME", "SparkTalksBot")

# Web dashboard auth
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "admin")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "sparktalks-dev-secret")

# Voice rooms (WebRTC anonymous)
VOICE_ROOM_EXPIRE_SECONDS = int(os.getenv("VOICE_ROOM_EXPIRE_SECONDS", "2700"))  # 45 min
VOICE_ROOM_BASE_URL = os.getenv("VOICE_ROOM_BASE_URL", "")  # e.g. https://your-app.onrender.com
# Legacy (no longer used for invites)
VOICE_ROOM_GROUP_ID = int(os.getenv("VOICE_ROOM_GROUP_ID", "0"))
VOICE_ROOM_INVITE_EXPIRE = int(os.getenv("VOICE_ROOM_INVITE_EXPIRE", "3600"))

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
MUTE_MESSAGE = "🔇 You are muted. Please wait."

# Referral
REFERRAL_REWARD_DAYS = 3

# VIP priority
VIP_PRIORITY_QUEUE = True

# Edit sync
SYNC_MESSAGE_EDITS = True
SYNC_MESSAGE_DELETES = True

# Image hash anti-spam
IMAGE_HASH_ENABLED = True
IMAGE_HASH_THRESHOLD = 6
IMAGE_HASH_MAX_SEEN = 5000
IMAGE_HASH_AUTO_MUTE_AFTER = 3

# Voice rooms
VOICE_ROOM_MIN_VIP = False

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
    "PLAN_14D": {"days": 14, "price_inr": "₹99", "price_usd": "$1.99",
                 "name": "🚀 Sprint VIP Pass", "label": "14 Days", "stars": 60},
    "PLAN_1M":  {"days": 30, "price_inr": "₹179", "price_usd": "$3.49",
                 "name": "🥇 Gold VIP Pass", "label": "1 Month", "stars": 110},
    "PLAN_3M":  {"days": 90, "price_inr": "₹449", "price_usd": "$8.49",
                 "name": "💎 Diamond VIP Pass", "label": "3 Months", "stars": 250},
    "PLAN_6M":  {"days": 180, "price_inr": "₹799", "price_usd": "$14.99",
                 "name": "🔥 Master VIP Pass", "label": "6 Months", "stars": 450},
}

BTN_FIND = "❤️ Find Partner"
BTN_SETTINGS = "⚙️ Settings"
BTN_VIP = "🛍️ VIP Store"
BTN_PROFILE = "👤 Profile"
BTN_NEXT = "🔄 Next"
BTN_END = "🛑 End Chat"
BTN_REPORT = "🚨 Report"
BTN_BLOCK = "🚫 Block"