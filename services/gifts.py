"""Gift tracking — log referral/VIP gifts + notify owner."""
import logging

from config import OWNER_ID
from utils import safe_send, utcnow, esc

logger = logging.getLogger("sparktalks")


# Lazy reference (avoid circular import at module load)
def _get_collection():
    try:
        from database import db
        if db is None:
            return None
        return db["gift_log"]
    except Exception as e:
        logger.debug(f"gift collection unavailable: {e}")
        return None


async def ensure_indexes():
    """Create indexes for gift_log (called from init_db)."""
    col = _get_collection()
    if col is None:
        return
    try:
        await col.create_index("user_id")
        await col.create_index("at")
        await col.create_index("gift_type")
        logger.info("✅ gift_log indexes ready")
    except Exception as e:
        logger.error(f"gift_log index creation failed: {e}")


async def log_gift(context, uid: int, gift_type: str, gift_value,
                   reason: str, referred_by: int = None, extra: dict = None):
    """
    Record a gift given to a user + notify owner.

    gift_type  : "VIP days" | "Referral reward" | "Bonus"
    gift_value : int (days) or string
    reason     : human-readable explanation
    """
    entry = {
        "user_id": uid,
        "gift_type": gift_type,
        "gift_value": gift_value,
        "reason": reason,
        "at": utcnow(),
        "referred_by": referred_by,
        "extra": extra or {},
    }

    # ─── Store in DB ──────────────────────────────────
    col = _get_collection()
    if col is not None:
        try:
            await col.insert_one(dict(entry))
        except Exception as e:
            logger.error(f"gift_log insert failed: {e}")

    # ─── Notify owner ─────────────────────────────────
    if OWNER_ID and context is not None:
        try:
            from database import get_user
            u = await get_user(uid)
            name = esc((u or {}).get("name") or "Unknown")
            uname = (u or {}).get("username")
            uname_str = esc(f"@{uname}") if uname else "—"

            body = (
                f"🎁  ✨  <b>Gift Sent</b>  ✨  🎁\n"
                f"▎\n"
                f"▎ 👤 User : <b>{name}</b>\n"
                f"▎ 🆔 ID : <code>{uid}</code>\n"
                f"▎ 🔗 Username : {uname_str}\n"
                f"▎\n"
                f"▎ 🎁 Gift : <b>+{gift_value} {gift_type}</b>\n"
                f"▎ 📝 Reason : {esc(reason)}\n"
            )
            if referred_by:
                body += f"▎ 👥 Referred by : <code>{referred_by}</code>\n"
            if extra:
                for k, v in extra.items():
                    body += f"▎ {esc(str(k))} : {esc(str(v))}\n"
            body += f"▎\n▎ 🕐 {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"

            await safe_send(context, OWNER_ID, body, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Owner gift notify failed: {e}")

    return True


async def get_user_gifts(uid: int, limit: int = 30) -> list:
    """Return list of gift docs received by a user (newest first)."""
    col = _get_collection()
    if col is None:
        return []
    try:
        cursor = col.find({"user_id": uid}).sort("at", -1).limit(limit)
        return [doc async for doc in cursor]
    except Exception as e:
        logger.error(f"get_user_gifts failed: {e}")
        return []


async def get_recent_gifts(limit: int = 20) -> list:
    """Return recent gifts across all users."""
    col = _get_collection()
    if col is None:
        return []
    try:
        cursor = col.find().sort("at", -1).limit(limit)
        return [doc async for doc in cursor]
    except Exception as e:
        logger.error(f"get_recent_gifts failed: {e}")
        return []


async def count_user_gifts(uid: int) -> int:
    col = _get_collection()
    if col is None:
        return 0
    try:
        return await col.count_documents({"user_id": uid})
    except Exception:
        return 0