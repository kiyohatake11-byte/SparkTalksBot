import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI, ADMIN_IDS, OWNER_ID, MAX_BLOCKED_USERS
from state import users, admin_cache
from utils import utcnow

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# DATABASE CONNECTION
# ══════════════════════════════════════════════════════════════
try:
    mongo_client = AsyncIOMotorClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
        socketTimeoutMS=20000,
        maxPoolSize=50,
        minPoolSize=5,
        retryWrites=True,
        retryReads=True,
        w="majority",
        journal=True,
    )
    db = mongo_client["sparktalks_db"]
    users_collection = db["users"]
    scheduled_collection = db["scheduled_broadcasts"]
    masked = MONGO_URI.split("@")[-1] if "@" in MONGO_URI else MONGO_URI
    logger.info(f"MongoDB client created (URI: {masked})")
except Exception as e:
    logger.critical(f"Failed to create MongoDB client: {e}")
    mongo_client = None
    db = None
    users_collection = None
    scheduled_collection = None


# ══════════════════════════════════════════════════════════════
# WRITE-BEHIND CACHE
# ══════════════════════════════════════════════════════════════
_pending_writes: dict = {}
_write_lock = asyncio.Lock()
MAX_WRITE_RETRIES = 5


# ══════════════════════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════════════════════

async def init_db():
    if users_collection is None:
        logger.error("MongoDB not connected. Skipping index creation.")
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
        await users_collection.create_index("referral_code")
        await users_collection.create_index("referred_by")
        if scheduled_collection is not None:
            await scheduled_collection.create_index("run_at")
        count = await users_collection.count_documents({})
        logger.info(f"✅ DB verified. Total users: {count}")
    except Exception as e:
        logger.error(f"❌ Index creation failed: {e}", exc_info=True)


async def safe_count(query: dict = None) -> int:
    if users_collection is None:
        return 0
    try:
        return await users_collection.count_documents(query or {})
    except Exception as e:
        logger.error(f"safe_count error: {e}")
        return 0


async def refresh_admin_cache():
    if users_collection is None:
        logger.error("Cannot refresh admin cache — DB not connected")
        return
    ids = set(ADMIN_IDS + ([OWNER_ID] if OWNER_ID else []))
    try:
        cursor = users_collection.find({"is_admin": True}, {"user_id": 1, "is_banned": 1})
        async for doc in cursor:
            if not doc.get("is_banned"):
                ids.add(doc["user_id"])
        admin_cache.clear()
        admin_cache.update(ids)
        logger.info(f"✅ Admin cache refreshed: {len(admin_cache)}")
    except Exception as e:
        logger.error(f"❌ Admin cache failed: {e}", exc_info=True)


async def is_owner_or_admin(user_id: int) -> bool:
    if user_id not in admin_cache:
        return False
    u = users.get(user_id)
    if u and u.get("is_banned"):
        return False
    return True


async def resolve_user(identifier: str):
    if users_collection is None or not identifier:
        return None, None
    identifier = identifier.strip().lstrip("@")
    try:
        uid = int(identifier)
        doc = await users_collection.find_one({"user_id": uid})
        if doc:
            return uid, doc
    except ValueError:
        pass
    doc = await users_collection.find_one(
        {"username": {"$regex": f"^{identifier}$", "$options": "i"}}
    )
    if doc:
        return doc["user_id"], doc
    return None, None


# ══════════════════════════════════════════════════════════════
# USER DEFAULTS
# ══════════════════════════════════════════════════════════════
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
    # NEW fields
    "timezone_offset": 5.5,
    "theme": None,
    "verified": False,
    "match_feedback": [],
    "payment_history": [],
}


# ══════════════════════════════════════════════════════════════
# LOAD USER
# ══════════════════════════════════════════════════════════════

async def load_user_from_db(user_id: int):
    if users_collection is None:
        return None
    try:
        doc = await users_collection.find_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"❌ Load user {user_id} failed: {e}", exc_info=True)
        return None
    if not doc:
        return None

    is_vip = bool(doc.get("is_vip", False))
    vip_expiry = doc.get("vip_expiry_date")
    if is_vip and vip_expiry and vip_expiry < utcnow():
        is_vip = False
        vip_expiry = None
        try:
            await users_collection.update_one(
                {"user_id": user_id},
                {"$set": {"is_vip": False, "vip_expiry_date": None,
                          "vip_tier_name": "None", "pref_gender": "Any"}},
            )
        except Exception:
            pass

    pref = doc.get("pref_gender", "Any")
    if not is_vip and pref != "Any":
        pref = "Any"

    blocked = doc.get("blocked_users", []) or []
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
        "interests": doc.get("interests", []) or [],
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
        "banned_reason": doc.get("banned_reason"),
        "banned_at": doc.get("banned_at"),
        "banned_by": doc.get("banned_by"),
        "timezone_offset": doc.get("timezone_offset", 5.5),
        "theme": doc.get("theme"),
        "verified": bool(doc.get("verified", False)),
        "match_feedback": doc.get("match_feedback", []) if isinstance(doc.get("match_feedback"), list) else [],
        "payment_history": doc.get("payment_history", []) if isinstance(doc.get("payment_history"), list) else [],
        "state": "IDLE",
        "partner": None,
        "temp": None,
        "pending_media": {},
        "awaiting_input": None,
        "recent_partners": [],
        "last_active": utcnow(),
    })
    return user


# ══════════════════════════════════════════════════════════════
# SAVE — Queue-based
# ══════════════════════════════════════════════════════════════

async def save_user_to_db(user_id: int, u: dict):
    if users_collection is None:
        return False
    snapshot = dict(u)
    snapshot["interests"] = list(u.get("interests") or [])
    snapshot["blocked_users"] = list(u.get("blocked_users") or [])
    snapshot["warnings"] = list(u.get("warnings") or [])
    snapshot["recent_partners"] = list(u.get("recent_partners") or [])
    snapshot["pending_media"] = dict(u.get("pending_media") or {})
    snapshot["match_feedback"] = list(u.get("match_feedback") or [])
    snapshot["payment_history"] = list(u.get("payment_history") or [])
    snapshot.pop("_retry_count", None)
    _pending_writes[user_id] = snapshot
    return True


async def _do_save(user_id: int, u: dict):
    if users_collection is None:
        return False
    try:
        blocked = u.get("blocked_users", []) or []
        if len(blocked) > MAX_BLOCKED_USERS:
            blocked = blocked[-MAX_BLOCKED_USERS:]
            u["blocked_users"] = blocked

        # Cap payment history to last 20
        ph = u.get("payment_history", []) or []
        if len(ph) > 20:
            ph = ph[-20:]

        mf = u.get("match_feedback", []) or []
        if len(mf) > 50:
            mf = mf[-50:]

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
                "banned_reason": u.get("banned_reason"),
                "banned_at": u.get("banned_at"),
                "banned_by": u.get("banned_by"),
                "timezone_offset": u.get("timezone_offset", 5.5),
                "theme": u.get("theme"),
                "verified": bool(u.get("verified", False)),
                "match_feedback": mf,
                "payment_history": ph,
                "last_active": utcnow(),
            },
            "$setOnInsert": {
                "joined_date": u.get("joined_date") or utcnow(),
            },
        }
        result = await users_collection.update_one(
            {"user_id": user_id}, update, upsert=True
        )
        if result.upserted_id:
            logger.debug(f"✅ User {user_id} CREATED")
        return True
    except Exception as e:
        logger.error(f"❌ DB write failed {user_id}: {e}", exc_info=True)
        return False


async def save_user_to_db_immediate(user_id: int, u: dict):
    return await _do_save(user_id, u)


async def flush_writes(context=None):
    if not _pending_writes or users_collection is None:
        return
    async with _write_lock:
        batch = dict(_pending_writes)
        _pending_writes.clear()

    count = 0
    failed_writes = {}
    dead_uids = []

    for uid, u in batch.items():
        ok = await _do_save(uid, u)
        if ok:
            count += 1
            continue
        retry = int(u.get("_retry_count", 0)) + 1
        if retry >= MAX_WRITE_RETRIES:
            dead_uids.append(uid)
        else:
            u["_retry_count"] = retry
            failed_writes[uid] = u

    if failed_writes:
        async with _write_lock:
            for uid, u in failed_writes.items():
                if uid not in _pending_writes:
                    _pending_writes[uid] = u
                else:
                    er = int(_pending_writes[uid].get("_retry_count", 0))
                    fr = int(u.get("_retry_count", 0))
                    _pending_writes[uid]["_retry_count"] = max(er, fr)
        logger.warning(f"⚠️ {len(failed_writes)} writes re-queued")

    if dead_uids:
        logger.error(f"💀 {len(dead_uids)} writes permanently failed: {dead_uids}")
    if count:
        logger.debug(f"💾 Flushed {count} user writes")


def _default_user_dict(name: str = None, username: str = None) -> dict:
    u = dict(USER_DEFAULTS)
    u.update({
        "name": name, "username": username,
        "joined_date": utcnow(),
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