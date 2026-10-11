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

# Image hash anti-spam
image_hash_map: dict = {}
image_hash_user_count: dict = {}

# Duplicate text detection
duplicate_text_map: dict = {}

# Media timeout tracking
pending_media_timeouts: dict = {}

# Voice rooms
active_voice_rooms: dict = {}

# Scheduled broadcasts
scheduled_broadcasts: list = []

# Saved partners
saved_partners: dict = {}

# Reconnect daily tracker
reconnect_daily: dict = {}

# Save partner requests
pending_save_requests: dict = {}

# ⭐ NEW: Deleted user IDs — blocks any writes/re-creation
deleted_user_ids: set = set()

# Analytics
analytics = {
    "matches_today": 0,
    "reports_today": 0,
    "violations_today": 0,
    "mutes_today": 0,
    "vip_purchases_today": 0,
    "broadcasts_sent": 0,
    "referrals_today": 0,
    "voice_rooms_today": 0,
    "reconnects_today": 0,
    "saves_today": 0,
    "start_time": None,
    "match_wait_total": 0,
    "match_wait_count": 0,
}