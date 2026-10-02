import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# ENVIRONMENT & CONFIG
# ──────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()]

# Limits & Intervals
MAX_REACTION_ENTRIES = 20000
MAX_BLOCKED_USERS = 100
MAX_RECENT_PARTNERS = 0
NEXT_COOLDOWN_SECONDS = 0
QUEUE_CLEANUP_INTERVAL = 300
VIP_CHECK_INTERVAL = 3600
INACTIVE_USER_TIMEOUT = 3600
CLEANUP_USERS_INTERVAL = 600
MAX_PENDING_MEDIA = 20

# Available Interests
AVAILABLE_INTERESTS = [
    "🎮 Gaming", "🎵 Music", "🎬 Movies",
    "💻 Tech", "⚽ Sports", "📚 Books",
    "🎨 Art", "✈️ Travel", "🍿 Anime"
]

# VIP Plans
VIP_PLANS = {
    "PLAN_14D": {
        "days": 14,
        "price_inr": "₹99",
        "price_usd": "$1.99",
        "name": "🚀 Sprint VIP Pass",
        "label": "14 Days",
        "stars": 60
    },
    "PLAN_1M": {
        "days": 30,
        "price_inr": "₹179",
        "price_usd": "$3.49",
        "name": "🥇 Gold VIP Pass",
        "label": "1 Month",
        "stars": 110
    },
    "PLAN_3M": {
        "days": 90,
        "price_inr": "₹449",
        "price_usd": "$8.49",
        "name": "💎 Diamond VIP Pass",
        "label": "3 Months",
        "stars": 250
    },
    "PLAN_6M": {
        "days": 180,
        "price_inr": "₹799",
        "price_usd": "$14.99",
        "name": "🔥 Master VIP Pass",
        "label": "6 Months",
        "stars": 450
    },
}

# Button Labels — Fancy Style
BTN_FIND = "❤️ Find Partner"
BTN_SETTINGS = "⚙️ Settings"
BTN_VIP = "🛍️ VIP Store"
BTN_PROFILE = "👤 Profile"
BTN_NEXT = "🔄 Next"
BTN_END = "🛑 End Chat"
BTN_REPORT = "🚨 Report"
BTN_BLOCK = "🚫 Block"