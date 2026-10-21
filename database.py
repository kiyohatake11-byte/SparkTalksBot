import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient

from config import (
    MONGO_URI, ADMIN_IDS, OWNER_ID, MAX_BLOCKED_USERS,
    MAX_SAVED_PARTNERS,
)
from state import users, admin_cache, deleted_user_ids, muted_users
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
    )
    db = mongo_client["sparktalks_db"]
    users_collection = db["users"]
    scheduled_collection = db["scheduled_broadcasts"]
    bad_words_collection = db["bad_words"]
    processed_payments_collection = db["processed_payments"]   # ⭐ NEW
    analytics_history_collection = db["analytics_history"]     # ⭐ NEW
    masked = MONGO_URI.split("@")[-1] if "@" in MONGO_URI else MONGO_URI
    logger.info(f"MongoDB client created (URI: {masked})")
except Exception as e:
    logger.critical(f"Failed to create MongoDB client: {e}")
    mongo_client = None
    db = None
    users_collection = None
    scheduled_collection = None
    bad_words_collection = None
    processed_payments_collection = None                       # ⭐ NEW
    analytics_history_collection = None                        # ⭐ NEW


# ══════════════════════════════════════════════════════════════
# WRITE-BEHIND CACHE
# ══════════════════════════════════════════════════════════════
_pending_writes: dict = {}
_write_lock = asyncio.Lock()
MAX_WRITE_RETRIES = 5


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
        if bad_words_collection is not None:
            await bad_words_collection.create_index("word", unique=True)
        # ⭐ Payment idempotency index
        if processed_payments_collection is not None:
            await processed_payments_collection.create_index(
                "charge_id", unique=True
            )
            await processed_payments_collection.create_index("at")
        # ⭐ Analytics history index
        if analytics_history_collection is not None:
            await analytics_history_collection.create_index(
                "date", unique=True
            )
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
    "timezone_offset": 5.5,
    "theme": None,
    "verified": False,
    "match_feedback": [],
    "payment_history": [],
    "saved_partner_ids": [],
    "saved_partner_nicks": [],
    "achievements": [],
    "needs_onboarding": False,
    "joined_community": False,
    "gender_pref_credits": 0,
    "referral_milestones_claimed": [],
}


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

    # ─── VIP expiry check ────────────────────────────────
    is_vip = bool(doc.get("is_vip", False))
    vip_expiry = doc.get("vip_expiry_date")
    if is_vip and vip_expiry and vip_expiry < utcnow():
        is_vip = False
        vip_expiry = None
        try:
            await users_collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "is_vip": False,
                    "vip_expiry_date": None,
                    "vip_tier_name": "None",
                    "pref_gender": "Any",
                }},
            )
        except Exception:
            pass

    # ─── Non-VIP users cannot keep gender pref ───────────
    pref = doc.get("pref_gender", "Any")
    if not is_vip and pref != "Any":
        pref = "Any"
        try:
            await users_collection.update_one(
                {"user_id": user_id},
                {"$set": {"pref_gender": "Any"}},
            )
        except Exception:
            pass

    # ─── Cap blocked users ───────────────────────────────
    blocked = doc.get("blocked_users", []) or []
    if not isinstance(blocked, list):
        blocked = []
    if len(blocked) > MAX_BLOCKED_USERS:
        blocked = blocked[-MAX_BLOCKED_USERS:]

    # ─── Sanitize list fields ────────────────────────────
    interests = doc.get("interests", []) or []
    if not isinstance(interests, list):
        interests = []

    warnings = doc.get("warnings", []) or []
    if not isinstance(warnings, list):
        warnings = []

    match_feedback = doc.get("match_feedback", []) or []
    if not isinstance(match_feedback, list):
        match_feedback = []

    payment_history = doc.get("payment_history", []) or []
    if not isinstance(payment_history, list):
        payment_history = []

    saved_partner_ids = doc.get("saved_partner_ids", []) or []
    if not isinstance(saved_partner_ids, list):
        saved_partner_ids = []
    try:
        saved_partner_ids = list(dict.fromkeys(
            int(x) for x in saved_partner_ids if x is not None
        ))
    except (ValueError, TypeError):
        saved_partner_ids = []
    if len(saved_partner_ids) > MAX_SAVED_PARTNERS:
        saved_partner_ids = saved_partner_ids[-MAX_SAVED_PARTNERS:]

    saved_partner_nicks = doc.get("saved_partner_nicks", []) or []
    if not isinstance(saved_partner_nicks, list):
        saved_partner_nicks = []
    saved_partner_nicks = [str(n)[:40] for n in saved_partner_nicks if n is not None]
    while len(saved_partner_nicks) < len(saved_partner_ids):
        saved_partner_nicks.append("")
    saved_partner_nicks = saved_partner_nicks[:len(saved_partner_ids)]

    achievements = doc.get("achievements", []) or []
    if not isinstance(achievements, list):
        achievements = []
    achievements = [a for a in achievements if isinstance(a, str)]
    achievements = list(dict.fromkeys(achievements))

    gender_credits = doc.get("gender_pref_credits", 0)
    if not isinstance(gender_credits, int) or gender_credits < 0:
        gender_credits = 0

    milestones = doc.get("referral_milestones_claimed", []) or []
    if not isinstance(milestones, list):
        milestones = []
    milestones = [int(m) for m in milestones if str(m).isdigit()]
    milestones = list(set(milestones))

    user = dict(USER_DEFAULTS)
    user.update({
        "name": doc.get("name"),
        "username": doc.get("username"),
        "gender": doc.get("gender"),
        "age": doc.get("age"),
        "country": doc.get("country"),
        "bio": doc.get("bio"),
        "interests": interests,
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
        "warnings": warnings,
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
        "match_feedback": match_feedback,
        "payment_history": payment_history,
        "saved_partner_ids": saved_partner_ids,
        "saved_partner_nicks": saved_partner_nicks,
        "achievements": achievements,
        "needs_onboarding": bool(doc.get("needs_onboarding", False)),
        "joined_community": bool(doc.get("joined_community", False)),
        "gender_pref_credits": gender_credits,
        "referral_milestones_claimed": milestones,
        "pending_pref_from_credit": False,
        "state": "IDLE",
        "partner": None,
        "temp": None,
        "pending_media": {},
        "awaiting_input": None,
        "recent_partners": [],
        "last_active": utcnow(),
        "_search_msg_id": None,
        "_last_broadcast": None,
    })

    try:
        muted_until = doc.get("muted_until")
        if muted_until and muted_until > utcnow():
            muted_users[user_id] = muted_until.timestamp()
    except Exception:
        pass

    return user


async def save_user_to_db(user_id: int, u: dict):
    if users_collection is None:
        return False
    if user_id in deleted_user_ids:
        logger.debug(f"Skip queueing write for deleted user {user_id}")
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


async def _do_save(user_id: int, u: dict) -> bool:
    if users_collection is None:
        return False

    if user_id in deleted_user_ids:
        logger.debug(f"⛔ Blocked write for deleted user {user_id}")
        return True

    try:
        blocked = u.get("blocked_users", []) or []
        if not isinstance(blocked, list):
            blocked = []
        if len(blocked) > MAX_BLOCKED_USERS:
            blocked = blocked[-MAX_BLOCKED_USERS:]
            u["blocked_users"] = blocked

        ph = u.get("payment_history", []) or []
        if not isinstance(ph, list):
            ph = []
        if len(ph) > 20:
            ph = ph[-20:]
            u["payment_history"] = ph

        mf = u.get("match_feedback", []) or []
        if not isinstance(mf, list):
            mf = []
        if len(mf) > 50:
            mf = mf[-50:]
            u["match_feedback"] = mf

        saved = u.get("saved_partner_ids", []) or []
        if not isinstance(saved, list):
            saved = []
        try:
            saved = list(dict.fromkeys(
                int(x) for x in saved if x is not None
            ))
        except (ValueError, TypeError):
            saved = []
        if len(saved) > MAX_SAVED_PARTNERS:
            saved = saved[-MAX_SAVED_PARTNERS:]
        u["saved_partner_ids"] = saved

        nicks = u.get("saved_partner_nicks", []) or []
        if not isinstance(nicks, list):
            nicks = []
        nicks = [str(n)[:40] for n in nicks if n is not None]
        while len(nicks) < len(saved):
            nicks.append("")
        nicks = nicks[:len(saved)]
        u["saved_partner_nicks"] = nicks

        achievements = u.get("achievements", []) or []
        if not isinstance(achievements, list):
            achievements = []
        achievements = [a for a in achievements if isinstance(a, str)]
        achievements = list(dict.fromkeys(achievements))
        u["achievements"] = achievements

        interests = u.get("interests", []) or []
        if not isinstance(interests, list):
            interests = []
        warnings = u.get("warnings", []) or []
        if not isinstance(warnings, list):
            warnings = []

        gender_credits = u.get("gender_pref_credits", 0)
        if not isinstance(gender_credits, int) or gender_credits < 0:
            gender_credits = 0
        milestones = u.get("referral_milestones_claimed", []) or []
        if not isinstance(milestones, list):
            milestones = []
        milestones = [int(m) for m in milestones if str(m).isdigit()]

        update = {
            "$set": {
                "user_id": user_id,
                "name": u.get("name"),
                "username": u.get("username"),
                "gender": u.get("gender"),
                "age": u.get("age"),
                "country": u.get("country"),
                "bio": u.get("bio"),
                "interests": interests,
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
                "warnings": warnings,
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
                "saved_partner_ids": saved,
                "saved_partner_nicks": nicks,
                "achievements": achievements,
                "needs_onboarding": bool(u.get("needs_onboarding", False)),
                "joined_community": bool(u.get("joined_community", False)),
                "gender_pref_credits": gender_credits,
                "referral_milestones_claimed": milestones,
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


async def _expire_vip_if_needed(uid: int, u: dict) -> None:
    if not u.get("is_vip"):
        return
    exp = u.get("vip_expiry_date")
    if exp is None:
        return
    now = utcnow()
    try:
        if exp < now:
            u["is_vip"] = False
            u["vip_expiry_date"] = None
            u["vip_tier_name"] = "None"
            if u.get("pref_gender") != "Any":
                u["pref_gender"] = "Any"
            await save_user_to_db(uid, u)
            logger.info(f"⌛ On-access VIP expired for {uid}")
    except Exception as e:
        logger.debug(f"VIP on-access check failed for {uid}: {e}")


async def get_user(uid: int):
    if uid not in users:
        db_user = await load_user_from_db(uid)
        if db_user:
            users[uid] = db_user
        else:
            deleted_user_ids.discard(uid)
            users[uid] = _default_user_dict()
            await save_user_to_db(uid, users[uid])
    u = users.get(uid)
    if u:
        u["last_active"] = utcnow()
        await _expire_vip_if_needed(uid, u)

        if uid not in muted_users:
            try:
                muted_until = u.get("muted_until")
                if muted_until and muted_until > utcnow():
                    muted_users[uid] = muted_until.timestamp()
            except Exception:
                pass
    return u


async def create_new_user(user_id: int, name: str = None, username: str = None) -> dict:
    deleted_user_ids.discard(user_id)
    new_user = _default_user_dict(name, username)
    users[user_id] = new_user
    await save_user_to_db(user_id, new_user)
    return new_user


# ══════════════════════════════════════════════════════════════
# BAD WORDS LOADER
# ══════════════════════════════════════════════════════════════

def _build_bad_words_regex():
    """Compile regex from state.bad_words. Called after load/add/remove."""
    import re
    import state as _st
    if not _st.bad_words:
        _st.bad_words_re = None
        return
    pattern = "|".join(re.escape(w["word"]) for w in _st.bad_words)
    try:
        _st.bad_words_re = re.compile(
            r"(?<![A-Za-z0-9\u0900-\u097F])(" + pattern + r")(?![A-Za-z0-9\u0900-\u097F])",
            re.IGNORECASE | re.UNICODE,
        )
    except Exception as e:
        logger.error(f"bad_words regex compile failed: {e}")
        _st.bad_words_re = None


async def load_bad_words() -> int:
    """Load bad words from DB into memory. Returns count."""
    import state as _st
    if bad_words_collection is None:
        _st.bad_words.clear()
        _st.bad_words_re = None
        return 0
    try:
        _st.bad_words.clear()
        cursor = bad_words_collection.find({})
        async for doc in cursor:
            _st.bad_words.append({
                "word": doc["word"],
                "action": doc.get("action", "ban"),
                "duration": int(doc.get("duration", 0) or 0),
            })
        _build_bad_words_regex()
        return len(_st.bad_words)
    except Exception as e:
        logger.error(f"load_bad_words failed: {e}", exc_info=True)
        return 0