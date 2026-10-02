import asyncio
from collections import deque, OrderedDict

# ──────────────────────────────────────────────────────────────
# RUNTIME STATE (Shared across modules)
# ──────────────────────────────────────────────────────────────
users: dict = {}
queue: deque = deque()
queue_lock = asyncio.Lock()
message_reactions_map = OrderedDict()
admin_cache: set = set()
last_next_time: dict = {}
