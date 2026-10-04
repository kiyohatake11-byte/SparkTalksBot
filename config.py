import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").replace("@", "").strip()
ADMIN_IDS = [
    int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()
]

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

AVAILABLE_INTERESTS = [
    "\U0001F3AE Gaming", "\U0001F3B5 Music", "\U0001F3AC Movies",
    "\U0001F4BB Tech", "\u26BD Sports", "\U0001F4DA Books",
    "\U0001F3A8 Art", "\u2708\uFE0F Travel", "\U0001F37F Anime",
]

VIP_PLANS = {
    "PLAN_14D": {
        "days": 14, "price_inr": "\u20B999", "price_usd": "$1.99",
        "name": "\U0001F680 Sprint VIP Pass", "label": "14 Days", "stars": 60,
    },
    "PLAN_1M": {
        "days": 30, "price_inr": "\u20B9179", "price_usd": "$3.49",
        "name": "\U0001F947 Gold VIP Pass", "label": "1 Month", "stars": 110,
    },
    "PLAN_3M": {
        "days": 90, "price_inr": "\u20B9449", "price_usd": "$8.49",
        "name": "\U0001F48E Diamond VIP Pass", "label": "3 Months", "stars": 250,
    },
    "PLAN_6M": {
        "days": 180, "price_inr": "\u20B9799", "price_usd": "$14.99",
        "name": "\U0001F525 Master VIP Pass", "label": "6 Months", "stars": 450,
    },
}

BTN_FIND = "\u2764\uFE0F Find Partner"
BTN_SETTINGS = "\u2699\uFE0F Settings"
BTN_VIP = "\U0001F6CD\uFE0F VIP Store"
BTN_PROFILE = "\U0001F464 Profile"
BTN_NEXT = "\U0001F504 Next"
BTN_END = "\U0001F6D1 End Chat"
BTN_REPORT = "\U0001F6A8 Report"
BTN_BLOCK = "\U0001F6AB Block"
