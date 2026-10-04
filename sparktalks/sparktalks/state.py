import asyncio
from collections import deque, OrderedDict

users: dict = {}
queue: deque = deque()
queue_set: set = set()
queue_lock = asyncio.Lock()
message_reactions_map: OrderedDict = OrderedDict()
admin_cache: set = set()
last_next_time: dict = {}
