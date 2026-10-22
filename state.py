"""Global state — v2 SIMPLE MATCHING.

⭐ CHANGES FROM v1:
  - Removed queue_buckets (no longer used)
  - Kept everything else
"""
import asyncio
from collections import deque, OrderedDict

# ══════════════════════════════════════════════════════════════
# USER STORAGE
# ══════════════════════════════════════════════════════════════
users: dict = {}

# ══════════════════════════════════════════════════════════════
# MATCHING QUEUE (v2 SIMPLE)
# ══════════════════════════════════════════════════════════════
queue: deque = deque()
queue_set: set = set()
queue_lock = asyncio.Lock()

# ══════════════════════════════════════════════════════════════
# BACKWARD COMPAT: empty queue_buckets (for any leftover code)
# ══════════════════════════════════════════════════════════════
queue_buckets: dict = {
    "Any":    {"Any": set(), "Male": set(), "Female": set()},
    "Male":   {"Any": set(), "Male": set(), "Female": set()},
    "Female": {"Any": set(), "Male": set(), "Female": set()},
}

# ══════════════════════════════════════════════════════════════
# MESSAGE MAPS
# ══════════════════════════════════════════════════════════════
message_reactions_map: OrderedDict = OrderedDict()
message_edit_map: OrderedDict = OrderedDict()

# ══════════════════════════════════════════════════════════════
# ADMIN & MODERATION
# ══════════════════════════════════════════════════════════════
admin_cache: set = set()
last_next_time: dict = {}
muted_users: dict = {}
maintenance_mode: bool = False
admin_logs: deque = deque(maxlen=200)

# ══════════════════════════════════════════════════════════════
# ANTI-SPAM
# ══════════════════════════════════════════════════════════════
flood_buckets: dict = {}
link_warn_count: dict = {}
image_hash_map: dict = {}
image_hash_user_count: dict = {}
duplicate_text_map: dict = {}
pending_media_timeouts: dict = {}

# ══════════════════════════════════════════════════════════════
# VOICE ROOMS
# ══════════════════════════════════════════════════════════════
active_voice_rooms: dict = {}

# ══════════════════════════════════════════════════════════════
# SCHEDULED BROADCASTS
# ══════════════════════════════════════════════════════════════
scheduled_broadcasts: list = []

# ══════════════════════════════════════════════════════════════
# SAVED PARTNERS
# ══════════════════════════════════════════════════════════════
saved_partners: dict = {}
reconnect_daily: dict = {}
pending_save_requests: dict = {}
save_request_tokens: dict = {}

# ══════════════════════════════════════════════════════════════
# USER DELETION TOMBSTONE
# ══════════════════════════════════════════════════════════════
deleted_user_ids: set = set()

# ══════════════════════════════════════════════════════════════
# COMMUNITY CHECK CACHE
# ══════════════════════════════════════════════════════════════
community_check_cache: dict = {}

# ══════════════════════════════════════════════════════════════
# BAD WORDS FILTER
# ══════════════════════════════════════════════════════════════
bad_words: list = []
bad_words_re = None

# ══════════════════════════════════════════════════════════════
# IDEMPOTENCY
# ══════════════════════════════════════════════════════════════
processed_updates: dict = {}

# ══════════════════════════════════════════════════════════════
# BROADCAST STATE
# ══════════════════════════════════════════════════════════════
broadcast_state = {
    "running": False,
    "stop_requested": False,
    "paused": False,
    "cancel_requested": False,
    "type": None,
    "started_at": None,
    "processed": 0,
    "total": 0,
    "sent": 0,
    "failed": 0,
    "admin_uid": None,
}

# ══════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════
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