"""Broadcast — worker pool + fail-fast + streaming ID fetch."""
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


async def _broadcast_send_text(context, chat_id: int, text: str,
                                reply_markup=None) -> bool:
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=None, reply_markup=reply_markup,
        )
        return True
    except RetryAfter as e:
        try:
            await asyncio.sleep(e.retry_after * BROADCAST_RETRY_FACTOR + 0.3)
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                parse_mode=None, reply_markup=reply_markup,
            )
            return True
        except Exception:
            return False
    except (Forbidden, BadRequest):
        return False
    except Exception:
        return False


async def _broadcast_send_media(context, chat_id: int,
                                 from_chat_id: int, message_id: int,
                                 reply_markup=None,
                                 caption: Optional[str] = None) -> bool:
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


async def _stream_user_ids_into(queue: asyncio.Queue) -> int:
    """Stream user IDs directly into queue (no full list in memory)."""
    if users_collection is None:
        return 0
    count = 0
    try:
        cursor = users_collection.find(
            {"is_banned": False}, {"user_id": 1, "_id": 0},
        )
        async for doc in cursor:
            uid = doc.get("user_id")
            if uid is not None:
                queue.put_nowait(uid)
                count += 1
    except Exception as e:
        logger.error(f"Stream user IDs failed: {e}")
    return count


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


async def _run_worker_pool(
    send_fn, btype: str, admin_uid: int,
) -> tuple:
    """Workers pull from a queue that's fed by streaming DB cursor."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
    _reset_state(btype, admin_uid, 0)

    async def _producer():
        try:
            total = await _stream_user_ids_into(queue)
            broadcast_state["total"] = total
        finally:
            # Sentinel: None signals workers to stop
            for _ in range(BROADCAST_WORKERS):
                queue.put_nowait(None)

    producer_task = asyncio.create_task(_producer())

    sent = 0
    failed = 0
    processed = 0
    last_progress_at = 0
    start_ts = utcnow().timestamp()

    logger.info(f"⚡ {btype.title()} broadcast starting | workers={BROADCAST_WORKERS}")

    async def worker():
        nonlocal sent, failed, processed, last_progress_at
        while True:
            if broadcast_state.get("cancel_requested"):
                return

            while broadcast_state.get("paused"):
                if broadcast_state.get("cancel_requested"):
                    return
                await asyncio.sleep(0.2)

            if broadcast_state.get("stop_requested"):
                return

            try:
                uid = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                if producer_task.done():
                    return
                continue

            if uid is None:
                return

            if broadcast_state.get("cancel_requested"):
                return

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

            total = broadcast_state["total"]
            if processed - last_progress_at >= BROADCAST_PROGRESS_EVERY:
                _log_progress(processed, total, start_ts, sent, failed)
                last_progress_at = processed

    tasks = [asyncio.create_task(worker()) for _ in range(BROADCAST_WORKERS)]
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        producer_task.cancel()
        try:
            await producer_task
        except Exception:
            pass
        _finish_state()

    elapsed = utcnow().timestamp() - start_ts
    rate = processed / elapsed if elapsed > 0 else 0
    analytics["broadcasts_sent"] += 1

    total = broadcast_state["total"]
    status = _final_status()
    logger.info(
        f"{status} {btype} broadcast in {elapsed:.1f}s — "
        f"sent={sent}, failed={failed}, processed={processed}/{total}, "
        f"avg rate={rate:.1f}/s"
    )
    return sent, failed


async def execute_broadcast(context, message: str, admin_uid: int = None,
                            reply_markup: Optional[InlineKeyboardMarkup] = None) -> tuple:
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

    async def send_fn(uid: int) -> bool:
        return await _broadcast_send_text(
            context, uid, message, reply_markup=reply_markup,
        )

    return await _run_worker_pool(send_fn, "text", admin_uid or 0)


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

    async def send_fn(uid: int) -> bool:
        return await _broadcast_send_media(
            context, uid, from_chat_id, message_id,
            reply_markup=reply_markup, caption=caption,
        )

    return await _run_worker_pool(send_fn, "media", admin_uid or 0)