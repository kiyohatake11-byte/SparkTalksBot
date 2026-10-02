import logging
from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI, ADMIN_IDS, OWNER_ID, MAX_BLOCKED_USERS
from state import users, admin_cache
from utils import utcnow

logger = logging.getLogger("sparktalks")

# ──────────────────────────────────────────────────────────────
# DATABASE CONNECTION
# ──────────────────────────────────────────────────────────────
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["sparktalks_db"]
users_collection = db["users"]


async def init_db():
    await users_collection.create_index("user_id", unique=True)
    await users_collection.create_index("is_vip")
    await users_collection.create_index("is_admin")
    await users_collection.create_index("is_banned")
    await users_collection.create_index("vip_expiry_date")


async def refresh_admin_cache():
    ids = set(ADMIN_IDS + ([OWNER_ID] if OWNER_ID else []))
    cursor = users_collection.find({"is_admin": True, "is_banned": False}, {"user_id": 1})
    async for doc in cursor:
        ids.add(doc["user_id"])
    admin_cache.clear()
    admin_cache.update(ids)
    logger.info(f"Admin cache refreshed: {len(admin_cache)} admins")


async def is_owner_or_admin(user_id: int) -> bool:
    if user_id in admin_cache:
        u = users.get(user_id)
        if u and u.get("is_banned"):
            return False
        return True
    return False


async def load_user_from_db(user_id: int):
    doc = await users_collection.find_one({"user_id": user_id})
    if not doc:
        return None

    is_vip = bool(doc.get("is_vip", False))
    vip_expiry = doc.get("vip_expiry_date")
    if is_vip and vip_expiry and vip_expiry < utcnow():
        is_vip = False
        vip_expiry = None
        await users_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "is_vip": False, "vip_expiry_date": None,
                "vip_tier_name": "None", "pref_gender": "Any"
            }}
        )

    pref = doc.get("pref_gender", "Any")
    if not is_vip and pref != "Any":
        pref = "Any"

    blocked = doc.get("blocked_users", [])
    if len(blocked) > MAX_BLOCKED_USERS:
        blocked = blocked[-MAX_BLOCKED_USERS:]

    return {
        "name": doc.get("name"),
        "username": doc.get("username"),
        "gender": doc.get("gender"),
        "age": doc.get("age"),
        "country": doc.get("country"),
        "bio": doc.get("bio"),
        "interests": doc.get("interests", []),
        "profile_public": bool(doc.get("profile_public", False)),
        "confirm_media": bool(doc.get("confirm_media", True)),
        "pref_gender": pref,
        "is_vip": is_vip,
        "vip_expiry_date": vip_expiry if is_vip else None,
        "vip_tier_name": doc.get("vip_tier_name", "None") if is_vip else "None",
        "is_admin": bool(doc.get("is_admin", False)),
        "is_banned": bool(doc.get("is_banned", False)),
        "blocked_users": blocked,
        "state": "IDLE",
        "partner": None,
        "temp": None,
        "pending_media": {},
        "awaiting_input": None,
        "recent_partners": [],
        "last_active": utcnow(),
    }


async def save_user_to_db(user_id: int, u: dict):
    blocked = u.get("blocked_users", [])
    if len(blocked) > MAX_BLOCKED_USERS:
        blocked = blocked[-MAX_BLOCKED_USERS:]
        u["blocked_users"] = blocked

    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "name": u.get("name"),
            "username": u.get("username"),
            "gender": u.get("gender"),
            "age": u.get("age"),
            "country": u.get("country"),
            "bio": u.get("bio"),
            "interests": u.get("interests", []),
            "profile_public": bool(u.get("profile_public", False)),
            "confirm_media": bool(u.get("confirm_media", True)),
            "pref_gender": u.get("pref_gender", "Any"),
            "is_vip": bool(u.get("is_vip", False)),
            "vip_expiry_date": u.get("vip_expiry_date"),
            "vip_tier_name": u.get("vip_tier_name", "None"),
            "is_admin": bool(u.get("is_admin", False)),
            "is_banned": bool(u.get("is_banned", False)),
            "blocked_users": blocked,
        }},
        upsert=True
    )


async def get_user(uid: int):
    if uid not in users:
        db_user = await load_user_from_db(uid)
        if db_user:
            users[uid] = db_user
    u = users.get(uid)
    if u:
        u["last_active"] = utcnow()
    return u
