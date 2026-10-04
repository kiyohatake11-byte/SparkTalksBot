import asyncio
from collections import deque, OrderedDict

users: dict = {}
queue: deque = deque()
queue_set: set = set()
queue_lock = asyncio.Lock()
message_reactions_map: OrderedDict = OrderedDict()
admin_cache: set = set()
last_next_time: dict = {}

# 🆕 Admin features
muted_users: dict = {}          # {user_id: unmute_timestamp}
maintenance_mode: bool = False   # Toggle for maintenance
admin_logs: deque = deque(maxlen=100)  # Recent admin actions