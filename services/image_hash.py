"""Perceptual image hashing — anti-spam for duplicate images."""
import logging
from io import BytesIO

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    IMAGE_HASH_ENABLED, IMAGE_HASH_THRESHOLD,
    IMAGE_HASH_MAX_SEEN, IMAGE_HASH_AUTO_MUTE_AFTER,
    FLOOD_MUTE_SECONDS,
)
from state import image_hash_map, image_hash_user_count, muted_users, analytics
from utils import safe_send, utcnow

logger = logging.getLogger("sparktalks")


def _compute_hash(image_bytes: bytes) -> str:
    try:
        import imagehash
        from PIL import Image
        img = Image.open(BytesIO(image_bytes))
        return str(imagehash.phash(img))
    except Exception as e:
        logger.debug(f"Image hash failed: {e}")
        return ""


def _hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 999
    return sum(1 for x, y in zip(a, b) if x != y)


async def check_image_duplicate(context: ContextTypes.DEFAULT_TYPE, uid: int,
                                u: dict, file_bytes: bytes) -> bool:
    """
    Returns True if duplicate (message should be blocked).
    Auto-mutes on repeated offence.
    Uses timestamped entries for proper cleanup.
    """
    if not IMAGE_HASH_ENABLED:
        return False

    h = _compute_hash(file_bytes)
    if not h:
        return False

    now_ts = utcnow().timestamp()

    # ─── Check close match ────────────────────────────
    for existing_hash, entry in list(image_hash_map.items()):
        uids = entry.get("uids", set()) if isinstance(entry, dict) else set()
        if _hamming(h, existing_hash) <= IMAGE_HASH_THRESHOLD:
            if uid in uids:
                # Same user re-sharing
                uc_entry = image_hash_user_count.get(uid)
                if not isinstance(uc_entry, dict):
                    uc_entry = {"count": 0, "last": now_ts}
                count = uc_entry.get("count", 0) + 1
                uc_entry["count"] = count
                uc_entry["last"] = now_ts
                image_hash_user_count[uid] = uc_entry

                if count >= IMAGE_HASH_AUTO_MUTE_AFTER:
                    muted_users[uid] = now_ts + FLOOD_MUTE_SECONDS
                    analytics["mutes_today"] += 1
                    await safe_send(
                        context, uid,
                        "🔇  ✨  <b>Auto-Muted</b>  ✨  🔇\n"
                        "▎\n"
                        "▎ 🖼️ Repeated image spamming detected.\n"
                        f"▎ ⏱ Muted for <b>{FLOOD_MUTE_SECONDS // 60} min</b>",
                        parse_mode="HTML",
                    )
                    if u.get("partner"):
                        try:
                            from services.matching import disconnect
                            await disconnect(context, uid, u["partner"], ender_id=uid)
                        except Exception:
                            pass
                    return True

                await safe_send(
                    context, uid,
                    "🖼️  ✨  <b>Duplicate Image</b>  ✨  🖼️\n"
                    "▎\n"
                    f"▎ ⚠️ Same image already shared ({count}/{IMAGE_HASH_AUTO_MUTE_AFTER}).\n"
                    "▎ 💡 Send unique content only.",
                    parse_mode="HTML",
                )
                return True
            else:
                # Different user, same image → add to set, don't block
                uids.add(uid)
                entry["uids"] = uids
                return False

    # ─── New hash — store with timestamp ──────────────
    image_hash_map[h] = {"uids": {uid}, "created": now_ts}

    if len(image_hash_map) > IMAGE_HASH_MAX_SEEN:
        for _ in range(len(image_hash_map) - IMAGE_HASH_MAX_SEEN):
            image_hash_map.pop(next(iter(image_hash_map)), None)

    return False


async def get_message_file_bytes(context: ContextTypes.DEFAULT_TYPE, msg) -> bytes:
    try:
        if msg.photo:
            f = await context.bot.get_file(msg.photo[-1].file_id)
        elif msg.sticker:
            f = await context.bot.get_file(msg.sticker.file_id)
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            f = await context.bot.get_file(msg.document.file_id)
        elif msg.animation:
            f = await context.bot.get_file(msg.animation.file_id)
        else:
            return b""
        buf = BytesIO()
        await f.download_to_memory(buf)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"File download failed: {e}")
        return b""