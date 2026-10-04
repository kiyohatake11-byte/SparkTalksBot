import logging
from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI, ADMIN_IDS, OWNER_ID, MAX_BLOCKED_USERS
from state import users, admin_cache
from utils import utcnow

logger = logging.getLogger("sparktalks")

try:
    mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client["sparktalks_db"]
    users_collection = db["users"]
    masked = MONGO_URI.split("@")[-1] if "@" in MONGO_URI else MONGO_URI
    logger.info(f"MongoDB client created (URI: {masked})")
except Exception as e:
    logger.critical(f"Failed to create MongoDB client: {e}")
    mongo_client = None
    db = None
    users_collection = None


async def init_db():
    if users_collection is None:
        return
    try:
        await users_collection.create_index("user_id", unique=True)
        await users_collection.create_index("username")
        await users_collection.create_index("is_vip")
        await users_collection.create_index("is_admin")
        await users_collection.create_index("is_banned")
        await users_collection.create_index("vip_expiry_date")
        await users_collection.create_index("last_active")
        await users_collection.create_index("muted_until")
        logger.info("✅ Database indexes created")

        count = await users_collection.count_documents({})
        logger.info(f"✅ DB verified. Total users: {count}")
    except Exception as e:
        logger.error(f"❌ Index creation failed: {e}", exc_info=True)


async def safe_count(query: dict = None) -> int:
    if users_collection is None:
        return 0
    try:
        return await users_collection.count_documents(query or {})
    except Exception:
        return 0


async def refresh_admin_cache():
    if users_collection is None:
        return
    ids = set(ADMIN_IDS + ([OWNER_ID] if OWNER_ID else []))
    try:
        cursor = users_collection.find(
            {"is_admin": True, "is_banned": False}, {"user_id": 1}
        )
        async for doc in cursor:
            ids.add(doc["user_id"])
        admin_cache.clear()
        admin_cache.update(ids)
        logger.info(f"✅ Admin cache refreshed: {len(admin_cache)} admins")
    except Exception as e:
        logger.error(f"❌ Admin cache failed: {e}")


async def is_owner_or_admin(user_id: int) -> bool:
    if user_id not in admin_cache:
        return False
    u = users.get(user_id)
    if u and u.get("is_banned"):
        return False
    return True


# ══════════════════════════════════════════════════════════════
# 🆕 RESOLVE USER — ID or @username
# ══════════════════════════════════════════════════════════════

async def resolve_user(identifier: str):
    """
    Resolve user by ID or @username.
    Returns (user_id, doc) or (None, None).
    """
    if users_collection is None or not identifier:
        return None, None

    identifier = identifier.strip().lstrip("@")

    # Try as numeric ID first
    try:
        uid = int(identifier)
        doc = await users_collection.find_one({"user_id": uid})
        if doc:
            return uid, doc
    except ValueError:
        pass

    # Try as username (case-insensitive)
    doc = await users_collection.find_one(
        {"username": {"$regex": f"^{identifier}$", "$options": "i"}}
    )
    if doc:
        return doc["user_id"], doc

    return None, None


USER_DEFAULTS = {
    "name": None, "username": None, "gender": None, "age": None,
    "country": None, "bio": None, "interests": [],
    "profile_public": False, "confirm_media": True, "pref_gender": "Any",
    "is_vip": False, "vip_expiry_date": None, "vip_tier_name": "None",
    "is_admin": False, "is_banned": False, "blocked_users": [],
    "language": "en", "joined_date": None, "total_chats": 0,
    "total_matches": 0, "warnings": [], "report_count": 0,
    "referral_code": None, "referred_by": None,
    "muted_until": None, "banned_reason": None,
    "banned_at": None, "banned_by": None,
}


async def load_user_from_db(user_id: int):
    if users_collection is None:
        return None
    try:
        doc = await users_collection.find_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"❌ Load failed {user_id}: {e}")
        return None

    if not doc:
        return None

    is_vip = bool(doc.get("is_vip", False))
    vip_expiry = doc.get("vip_expiry_date")
    if is_vip and vip_expiry and vip_expiry < utcnow():
        is_vip = False
        vip_expiry = None

    pref = doc.get("pref_gender", "Any")
    if not is_vip and pref != "Any":
        pref = "Any"

    blocked = doc.get("blocked_users", [])
    if len(blocked) > MAX_BLOCKED_USERS:
        blocked = blocked[-MAX_BLOCKED_USERS:]

    user = dict(USER_DEFAULTS)
    user.update({
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
        "vip_tier_name": (doc.get("vip_tier_name") or "None") if is_vip else "None",
        "is_admin": bool(doc.get("is_admin", False)),
        "is_banned": bool(doc.get("is_banned", False)),
        "blocked_users": blocked,
        "language": doc.get("language", "en"),
        "joined_date": doc.get("joined_date"),
        "total_chats": doc.get("total_chats", 0),
        "total_matches": doc.get("total_matches", 0),
        "warnings": doc.get("warnings", []) if isinstance(doc.get("warnings"), list) else [],
        "report_count": doc.get("report_count", 0),
        "referral_code": doc.get("referral_code"),
        "referred_by": doc.get("referred_by"),
        "muted_until": doc.get("muted_until"),
        "state": "IDLE",
        "partner": None,
        "temp": None,
        "pending_media": {},
        "awaiting_input": None,
        "recent_partners": [],
        "last_active": utcnow(),
    })
    return user


async def save_user_to_db(user_id: int, u: dict):
    if users_collection is None:
        return False
    try:
        blocked = u.get("blocked_users", [])
        if len(blocked) > MAX_BLOCKED_USERS:
            blocked = blocked[-MAX_BLOCKED_USERS:]

        update = {
            "$set": {
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
                "language": u.get("language", "en"),
                "total_chats": u.get("total_chats", 0),
                "total_matches": u.get("total_matches", 0),
                "warnings": u.get("warnings", []),
                "report_count": u.get("report_count", 0),
                "referral_code": u.get("referral_code"),
                "referred_by": u.get("referred_by"),
                "muted_until": u.get("muted_until"),
                "last_active": utcnow(),
            },
            "$setOnInsert": {
                "joined_date": u.get("joined_date") or utcnow(),
            },
        }
        await users_collection.update_one({"user_id": user_id}, update, upsert=True)
        return True
    except Exception as e:
        logger.error(f"❌ Save failed {user_id}: {e}", exc_info=True)
        return False


def _default_user_dict(name: str = None, username: str = None) -> dict:
    u = dict(USER_DEFAULTS)
    u.update({
        "name": name, "username": username, "joined_date": utcnow(),
        "state": "IDLE", "partner": None, "temp": None,
        "pending_media": {}, "awaiting_input": None,
        "recent_partners": [], "last_active": utcnow(),
    })
    return u


async def get_user(uid: int):
    if uid not in users:
        db_user = await load_user_from_db(uid)
        if db_user:
            users[uid] = db_user
        else:
            users[uid] = _default_user_dict()
            await save_user_to_db(uid, users[uid])
    u = users.get(uid)
    if u:
        u["last_active"] = utcnow()
    return u


async def create_new_user(user_id: int, name: str = None, username: str = None) -> dict:
    new_user = _default_user_dict(name, username)
    users[user_id] = new_user
    await save_user_to_db(user_id, new_user)
    return new_user