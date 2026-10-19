"""Broadcast — best-case speed: worker pool + fail-fast sends.

Speed architecture:
  - 35 continuous workers (Telegram's ~30/sec global limit + burst headroom)
  - Fail-fast sends (no wasted sleeps on normal errors)
  - RetryAfter handled per-worker (only blocks that worker, not the batch)
  - Zero batch delays — workers never idle
  - Fast ID fetch via .to_list() (faster than async-for on Motor)
  - Optional pass-through stop/pause/cancel support

Expected: ~28-30 users/sec sustained (Telegram API ceiling).
"""

import asyncio
import logging
from typing import Optional

from telegram import InlineKeyboardMarkup
from telegram.error import RetryAfter, Forbidden, BadRequest

from config import (
    BROADCAST_WORKERS,
    BROADCAST_PROGRESS_EVERY,
    BROADCAST_RETRY_FACTOR,
)
from database import users_collection
from utils import safe_send, utcnow
from state import analytics, broadcast_state

logger = logging.getLogger("sparktalks")


# ══════════════════════════════════════════════════════════════
# RATE LIMIT — Admin can only broadcast every 60s
# ══════════════════════════════════════════════════════════════

_BROADCAST_MIN_INTERVAL = 60
_last_broadcast_at: dict = {}


def _check_rate_limit(admin_uid: int) -> int:
    last = _last_broadcast_at.get(admin_uid, 0)
    elapsed = utcnow().timestamp() - last
    if elapsed < _BROADCAST_MIN_INTERVAL:
        return int(_BROADCAST_MIN_INTERVAL - elapsed)
    return 0


def _mark_broadcast(admin_uid: int):
    _last_broadcast_at[admin_uid] = utcnow().timestamp()


# ══════════════════════════════════════════════════════════════
# BEST-CASE SEND — fail-fast, retry only on rate limit
# ══════════════════════════════════════════════════════════════

async def _broadcast_send_text(context, chat_id: int, text: str,
                                reply_markup=None) -> bool:
    """Broadcast-optimized text send.

    - No sleep on generic errors (fail-fast, frees worker immediately)
    - Retry ONLY on RetryAfter (rate limit), which is mandatory
    - Returns True on success, False on any failure
    """
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=None,
            reply_markup=reply_markup,
        )
        return True
    except RetryAfter as e:
        # Telegram forced us to wait — mandatory
        try:
            await asyncio.sleep(e.retry_after * BROADCAST_RETRY_FACTOR + 0.3)
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                reply_markup=reply_markup,
            )
            return True
        except Exception:
            return False
    except (Forbidden, BadRequest):
        # Blocked bot / invalid — no retry
        return False
    except Exception:
        # Network hiccup — fail fast, don't block worker
        return False


async def _broadcast_send_media(context, chat_id: int,
                                 from_chat_id: int, message_id: int,
                                 reply_markup=None,
                                 caption: Optional[str] = None) -> bool:
    """Broadcast-optimized media send."""
    kwargs = {
        "chat_id": chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup,
    }
    if caption:
        kwargs["caption"] = caption

    try:
        await context.bot.copy_message(**kwargs)
        return True
    except RetryAfter as e:
        try:
            await asyncio.sleep(e.retry_after * BROADCAST_RETRY_FACTOR + 0.3)
            await context.bot.copy_message(**kwargs)
            return True
        except Exception:
            return False
    except (Forbidden, BadRequest):
        return False
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

async def _fetch_user_ids() -> list:
    """Fetch all non-banned user IDs — optimized for speed.

    Uses .to_list() which returns a list directly (faster than async-for
    on Motor cursor for pure ID fetches).
    """
    if users_collection is None:
        return []
    try:
        docs = await users_collection.find(
            {"is_banned": False},
            {"user_id": 1, "_id": 0},
        ).to_list(length=None)
        return [doc["user_id"] for doc in docs if "user_id" in doc]
    except Exception as e:
        logger.error(f"Failed to fetch user IDs: {e}")
        return []


def _log_progress(processed: int, total: int, start_ts: float,
                  sent: int, failed: int):
    elapsed = utcnow().timestamp() - start_ts
    rate = processed / elapsed if elapsed > 0 else 0
    logger.info(
        f"📢 Progress: {processed}/{total} "
        f"({rate:.1f}/s, sent={sent}, failed={failed})"
    )


def _reset_state(btype: str, admin_uid: int, total: int):
    broadcast_state.update({
        "running": True,
        "stop_requested": False,
        "paused": False,
        "cancel_requested": False,
        "type": btype,
        "started_at": utcnow(),
        "processed": 0,
        "total": total,
        "sent": 0,
        "failed": 0,
        "admin_uid": admin_uid,
    })


def _finish_state():
    broadcast_state["running"] = False
    broadcast_state["paused"] = False
    broadcast_state["stop_requested"] = False
    broadcast_state["cancel_requested"] = False


def _final_status() -> str:
    if broadcast_state.get("cancel_requested"):
        return "🚫 Cancelled"
    if broadcast_state.get("stop_requested"):
        return "🛑 Stopped"
    return "✅ Done"


# ══════════════════════════════════════════════════════════════
# CORE WORKER POOL — used by both text and media
# ══════════════════════════════════════════════════════════════

async def _run_worker_pool(
    user_ids: list,
    send_fn,
    btype: str,
    admin_uid: int,
) -> tuple:
    """N workers continuously drain the queue. No batch delays."""
    total = len(user_ids)
    _reset_state(btype, admin_uid, total)

    num_workers = min(BROADCAST_WORKERS, total)
    logger.info(
        f"⚡ {btype.title()} broadcast → {total} users "
        f"| workers={num_workers} | fail-fast | no batch delay"
    )
    start_ts = utcnow().timestamp()

    # Fill queue
    queue: asyncio.Queue = asyncio.Queue()
    for uid in user_ids:
        queue.put_nowait(uid)

    sent = 0
    failed = 0
    processed = 0
    last_progress_at = 0

    async def worker():
        nonlocal sent, failed, processed, last_progress_at

        while True:
            # Cancel — highest priority
            if broadcast_state.get("cancel_requested"):
                return

            # Pause — spin-wait until unpaused or cancelled
            while broadcast_state.get("paused"):
                if broadcast_state.get("cancel_requested"):
                    return
                await asyncio.sleep(0.2)

            # Stop — graceful exit
            if broadcast_state.get("stop_requested"):
                return

            # Pull next user
            try:
                uid = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            # One more cancel check before hitting Telegram
            if broadcast_state.get("cancel_requested"):
                return

            # Send (fail-fast)
            try:
                ok = await send_fn(uid)
            except Exception:
                ok = False

            if ok:
                sent += 1
            else:
                failed += 1

            processed += 1
            broadcast_state["processed"] = processed
            broadcast_state["sent"] = sent
            broadcast_state["failed"] = failed

            if processed - last_progress_at >= BROADCAST_PROGRESS_EVERY:
                _log_progress(processed, total, start_ts, sent, failed)
                last_progress_at = processed

    # Spawn all workers
    tasks = [asyncio.create_task(worker()) for _ in range(num_workers)]

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _finish_state()

    # Final log
    elapsed = utcnow().timestamp() - start_ts
    rate = processed / elapsed if elapsed > 0 else 0
    analytics["broadcasts_sent"] += 1

    status = _final_status()
    logger.info(
        f"{status} {btype} broadcast in {elapsed:.1f}s — "
        f"sent={sent}, failed={failed}, processed={processed}/{total}, "
        f"avg rate={rate:.1f}/s"
    )
    return sent, failed


# ══════════════════════════════════════════════════════════════
# TEXT BROADCAST
# ══════════════════════════════════════════════════════════════

async def execute_broadcast(context, message: str, admin_uid: int = None,
                            reply_markup: Optional[InlineKeyboardMarkup] = None) -> tuple:
    # Rate limit
    if admin_uid is not None:
        wait = _check_rate_limit(admin_uid)
        if wait > 0:
            await safe_send(
                context, admin_uid,
                f"⏱ <i>Please wait {wait}s before broadcasting again.</i>",
                parse_mode="HTML",
            )
            return 0, 0
        _mark_broadcast(admin_uid)

    if users_collection is None:
        logger.error("Broadcast cancelled — DB down")
        return 0, 0

    if broadcast_state.get("running"):
        logger.warning("Broadcast blocked — another one running")
        return 0, 0

    user_ids = await _fetch_user_ids()
    if not user_ids:
        return 0, 0

    async def send_fn(uid: int) -> bool:
        return await _broadcast_send_text(
            context, uid, message, reply_markup=reply_markup,
        )

    return await _run_worker_pool(
        user_ids, send_fn, "text", admin_uid or 0,
    )


# ══════════════════════════════════════════════════════════════
# MEDIA BROADCAST
# ══════════════════════════════════════════════════════════════

async def execute_media_broadcast(context, from_chat_id: int, message_id: int,
                                   admin_uid: int = None,
                                   reply_markup: Optional[InlineKeyboardMarkup] = None,
                                   caption: Optional[str] = None) -> tuple:
    if admin_uid is not None:
        wait = _check_rate_limit(admin_uid)
        if wait > 0:
            await safe_send(
                context, admin_uid,
                f"⏱ <i>Please wait {wait}s before broadcasting again.</i>",
                parse_mode="HTML",
            )
            return 0, 0
        _mark_broadcast(admin_uid)

    if users_collection is None:
        return 0, 0

    if broadcast_state.get("running"):
        logger.warning("Media broadcast blocked — another one running")
        return 0, 0

    user_ids = await _fetch_user_ids()
    if not user_ids:
        return 0, 0

    async def send_fn(uid: int) -> bool:
        return await _broadcast_send_media(
            context, uid, from_chat_id, message_id,
            reply_markup=reply_markup, caption=caption,
        )

    return await _run_worker_pool(
        user_ids, send_fn, "media", admin_uid or 0,
    )