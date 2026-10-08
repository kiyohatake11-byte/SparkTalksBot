import asyncio
from collections import deque, OrderedDict

users: dict = {}
queue: deque = deque()
queue_set: set = set()
queue_lock = asyncio.Lock()
message_reactions_map: OrderedDict = OrderedDict()
admin_cache: set = set()
last_next_time: dict = {}

# Admin features
muted_users: dict = {}
maintenance_mode: bool = False
admin_logs: deque = deque(maxlen=200)

# Anti-Link
flood_buckets: dict = {}
link_warn_count: dict = {}

# Message edit sync
message_edit_map: OrderedDict = OrderedDict()

# Image hash anti-spam: hash_str -> set of uids
image_hash_map: dict = {}
image_hash_user_count: dict = {}  # uid -> count of duplicate shares

# Voice rooms
active_voice_rooms: dict = {}  # room_id -> {"u1": int, "u2": int, "invite": str, "created": ts}

# Scheduled broadcasts
scheduled_broadcasts: list = []  # list of dicts {run_at, message, media, chat_id, msg_id, created_by, id}

# Analytics (in-memory)
analytics = {
    "matches_today": 0,
    "reports_today": 0,
    "violations_today": 0,
    "mutes_today": 0,
    "vip_purchases_today": 0,
    "broadcasts_sent": 0,
    "referrals_today": 0,
    "voice_rooms_today": 0,
    "start_time": None,
    # ⭐ NEW — For estimated wait time calculation
    "match_wait_total": 0,   # sum of all wait seconds
    "match_wait_count": 0,   # number of matches measured
}