"""Anti-link / anti-username / anti-flood / mute / bad-word filter.
+ INSTANT report notifications to owner + all admins.
+ LOG_CHANNEL integration for all moderation events.
"""
import re
import logging
import time
from collections import deque, Counter
from datetime import timedelta

from telegram.ext import ContextTypes

from config import (
    ANTI_LINK_ENABLED, ANTI_LINK_WARN_LIMIT,
    FLOOD_MSG_LIMIT, FLOOD_WINDOW_SECONDS, FLOOD_MUTE_SECONDS,
    OWNER_ID, ADMIN_IDS,
)
from state import (
    muted_users, flood_buckets, link_warn_count,
    users, queue, queue_set, queue_lock, admin_cache, analytics,
)
from utils import safe_send, utcnow, esc, log_event

logger = logging.getLogger("sparktalks")


_URL_RE = re.compile(r"(https?://|www\.|ftp://)[^\s]+", re.IGNORECASE)
_TG_RE = re.compile(r"(t\.me/|telegram\.me/|telegram\.dog/|tg://)[^\s]+", re.IGNORECASE)
_USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z][A-Za-z0-9_]{3,31}(?![A-Za-z0-9_])")
_OBFUSCATED_AT = re.compile(r"(\(|\[|\{)\s*(at|@|एट)\s*(\)|\]|\})", re.IGNORECASE)
_OBFUSCATED_DOT = re.compile(r"(\(|\[|\{)\s*(dot|\.|डॉट)\s*(\)|\]|\})", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<![\d])(\+?\d[\d\s\-\(\)]{8,15}\d)(?![\d])")

_SOCIAL_KEYWORDS = re.compile(
    r"\b(instagram|insta|snapchat|snap|whatsapp|wa\.me|discord|kik|"
    r"facebook|fb|telegram|signal|vk|twitter|tiktok|onlyfans|"
    r"kik me|dm me|message me on|text me on|add me on|"
    r"my insta|my snap|my number|mera number|meri id|"
    r"मेरा नंबर|मेरी आईडी|मेरा इंस्टा)\b",
    re.IGNORECASE,
)


def _normalize_obfuscated(text: str) -> str:
    text = _OBFUSCATED_AT.sub(" @ ", text)
    text = _OBFUSCATED_DOT.sub(" . ", text)
    return text


def contains_contact_info(text: str) -> tuple[bool, str]:
    if not text:
        return False, ""

    if _URL_RE.search(text):
        return True, "link"
    if _TG_RE.search(text):
        return True, "telegram_link"
    if _USERNAME_RE.search(text):
        return True, "username"

    if _SOCIAL_KEYWORDS.search(text):
        norm = _normalize_obfuscated(text)
        if _URL_RE.search(norm) or _USERNAME_RE.search(norm) or _PHONE_RE.search(norm):
            return True, "social_promo"
        if re.search(r"\b(dm|contact|add|message|text|follow)\b", text, re.IGNORECASE):
            if "@" in text or re.search(r"\b\d{6,}\b", text):
                return True, "social_promo"

    norm = _normalize_obfuscated(text)
    if norm != text:
        if re.search(r"\S+\s*@\s*\S+\.\S+", norm) or _PHONE_RE.search(norm):
            return True, "obfuscated_contact"

    m = _PHONE_RE.search(text)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 10:
            return True, "phone"

    return False, ""


def is_flooding(uid: int) -> bool:
    now = utcnow().timestamp()
    bucket = flood_buckets.get(uid)
    if bucket is None:
        bucket = deque(maxlen=FLOOD_MSG_LIMIT + 5)
        flood_buckets[uid] = bucket
    bucket.append(now)
    cutoff = now - FLOOD_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return len(bucket) > FLOOD_MSG_LIMIT


def is_muted(uid: int) -> bool:
    unmute_at = muted_users.get(uid)
    if unmute_at is None:
        return False
    if utcnow().timestamp() >= unmute_at:
        muted_users.pop(uid, None)
        return False
    return True


def mute_remaining_seconds(uid: int) -> int:
    unmute_at = muted_users.get(uid)
    if unmute_at is None:
        return 0
    return max(0, int(unmute_at - utcnow().timestamp()))


async def handle_violation(context: ContextTypes.DEFAULT_TYPE, uid: int, u: dict,
                           reason: str, msg_text: str):
    if u.get("is_vip"):
        await safe_send(
            context, uid,
            "🛡️  ✨  <b>Contact Sharing Blocked</b>  ✨  🛡️\n"
            "▎\n"
            "▎ ⚠️ Your message was blocked (safety policy).\n"
            "▎\n"
            f"▎ 🚫 Detected : {reason.replace('_', ' ').title()}\n"
            "▎\n"
            "▎ 💡 <i>Contact info is not allowed in chat.</i>",
            parse_mode="HTML",
        )
        return

    count = link_warn_count.get(uid, 0) + 1
    link_warn_count[uid] = count

    if count >= ANTI_LINK_WARN_LIMIT:
        muted_users[uid] = utcnow().timestamp() + FLOOD_MUTE_SECONDS
        link_warn_count[uid] = 0
        analytics["mutes_today"] += 1

        if u.get("partner"):
            try:
                from services.matching import disconnect
                await disconnect(context, uid, u["partner"], ender_id=uid)
            except Exception:
                pass

        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
        if u.get("state") == "SEARCHING":
            u["state"] = "IDLE"

        try:
            from services.matching import _delete_searching_card
            await _delete_searching_card(context, uid)
        except Exception:
            pass

        minutes = FLOOD_MUTE_SECONDS // 60
        await safe_send(
            context, uid,
            "🔇  ✨  <b>Auto-Muted</b>  ✨  🔇\n"
            "▎\n"
            "▎ ⚠️ Too many contact-sharing violations.\n"
            "▎\n"
            f"▎ ⏱ Muted for <b>{minutes} min</b>\n"
            "▎\n"
            "▎ 💡 <i>Repeated violations → permanent ban.</i>",
            parse_mode="HTML",
        )

        # ⭐ Log to channel
        try:
            await log_event(
                context, "AUTO_MUTE",
                category="moderation", level="warn",
                target_id=uid,
                details={
                    "Reason": "Anti-link violations",
                    "Violations": count,
                    "Duration": f"{minutes} min",
                },
            )
        except Exception:
            pass

        logger.warning(f"Auto-muted {uid}")
        return

    remaining = ANTI_LINK_WARN_LIMIT - count
    await safe_send(
        context, uid,
        "⚠️  ✨  <b>Contact Sharing Not Allowed</b>  ✨  ⚠️\n"
        "▎\n"
        "▎ 🚫 Your message was blocked.\n"
        "▎\n"
        f"▎ Detected : {reason.replace('_', ' ').title()}\n"
        f"▎ Warning {count}/{ANTI_LINK_WARN_LIMIT}\n"
        f"▎ {remaining} more → auto-mute {FLOOD_MUTE_SECONDS // 60} min\n"
        "▎\n"
        "▎ 🛡️ <i>SparkTalks blocks links, usernames, phone numbers, social handles.</i>",
        parse_mode="HTML",
    )


def is_contact_share(msg) -> bool:
    return bool(getattr(msg, "contact", None))


def contains_bad_word(text: str):
    if not text:
        return False, None, None
    import state as _st
    if not _st.bad_words or _st.bad_words_re is None:
        return False, None, None
    try:
        m = _st.bad_words_re.search(text)
    except Exception:
        return False, None, None
    if not m:
        return False, None, None
    matched = m.group(1).lower()
    for rule in _st.bad_words:
        if rule["word"].lower() == matched:
            return True, matched, rule
    return True, matched, {"word": matched, "action": "ban", "duration": 0}


async def handle_bad_word_violation(context, uid: int, u: dict,
                                     matched: str, rule: dict) -> None:
    from state import (
        queue, queue_set, queue_lock, muted_users,
        analytics, admin_logs,
    )
    from database import users_collection, save_user_to_db

    action = rule.get("action", "ban")
    word = rule.get("word", matched)
    duration = int(rule.get("duration", 0) or 0)

    if u.get("partner"):
        try:
            from services.matching import disconnect
            await disconnect(context, uid, u["partner"], ender_id=uid)
        except Exception as e:
            logger.debug(f"bad_word disconnect failed: {e}")

    try:
        from services.matching import _delete_searching_card
        await _delete_searching_card(context, uid)
    except Exception:
        pass

    try:
        async with queue_lock:
            try:
                queue.remove(uid)
            except ValueError:
                pass
            queue_set.discard(uid)
    except Exception:
        pass
    u["state"] = "IDLE"

    try:
        admin_logs.append({
            "time": utcnow().strftime("%H:%M"),
            "admin": 0,
            "action": f"AUTO-{action.upper()}: '{word}' → {uid}",
        })
    except Exception:
        pass

    # ═══ BAN ═══
    if action == "ban":
        u["is_banned"] = True
        u["banned_reason"] = f"Auto-ban: prohibited word '{word}'"
        u["banned_at"] = utcnow()
        u["banned_by"] = 0

        if users_collection is not None:
            try:
                await users_collection.update_one(
                    {"user_id": uid},
                    {"$set": {
                        "is_banned": True,
                        "banned_reason": f"Auto-ban: prohibited word '{word}'",
                        "banned_at": utcnow(),
                        "banned_by": 0,
                    }},
                )
            except Exception as e:
                logger.error(f"bad_word ban DB write failed: {e}")

        try:
            await save_user_to_db(uid, u)
        except Exception:
            pass

        analytics["violations_today"] += 1

        await safe_send(
            context, uid,
            "🚫  ✨  <b>Access Denied</b>  ✨  🚫\n"
            "▎\n"
            "▎ You have been <b>permanently banned</b>.\n"
            "▎\n"
            "▎ 📝 Reason : Prohibited content detected\n"
            "▎\n"
            "💡 <i>Use /appeal to request review.</i>",
            parse_mode="HTML",
        )

        # ⭐ Log to channel
        try:
            await log_event(
                context, "AUTO_BAN",
                category="moderation", level="critical",
                target_id=uid,
                details={
                    "Word": word,
                    "Rule": "auto-ban",
                },
            )
        except Exception:
            pass

        logger.warning(f"🚫 AUTO-BAN: {uid} used prohibited word '{word}'")

    # ═══ MUTE ═══
    elif action == "mute":
        if duration <= 0:
            duration = 300

        unmute_at = utcnow() + timedelta(seconds=duration)
        muted_users[uid] = utcnow().timestamp() + duration
        u["muted_until"] = unmute_at

        if users_collection is not None:
            try:
                await users_collection.update_one(
                    {"user_id": uid},
                    {"$set": {"muted_until": unmute_at}},
                )
            except Exception as e:
                logger.error(f"bad_word mute DB write failed: {e}")

        try:
            await save_user_to_db(uid, u)
        except Exception:
            pass

        analytics["violations_today"] += 1
        analytics["mutes_today"] += 1

        minutes = duration // 60
        time_str = f"{minutes} min" if minutes >= 1 else f"{duration}s"

        await safe_send(
            context, uid,
            "🔇  ✨  <b>Muted</b>  ✨  🔇\n"
            "▎\n"
            "▎ Prohibited content detected.\n"
            "▎\n"
            f"▎ ⏱ Muted for <b>{time_str}</b>\n"
            "▎\n"
            "💡 <i>Repeated violations → permanent ban.</i>",
            parse_mode="HTML",
        )

        # ⭐ Log to channel
        try:
            await log_event(
                context, "AUTO_MUTE",
                category="moderation", level="warn",
                target_id=uid,
                details={
                    "Word": word,
                    "Duration": time_str,
                    "Rule": "auto-mute",
                },
            )
        except Exception:
            pass

        logger.warning(
            f"🔇 AUTO-MUTE: {uid} used prohibited word '{word}' for {time_str}"
        )


# ══════════════════════════════════════════════════════════════
# ⭐ INSTANT REPORT NOTIFICATIONS (owner + all admins) + DB
# ══════════════════════════════════════════════════════════════

_recent_report_alert: dict = {}
_REPORT_DEDUP_SECONDS = 60


def _should_notify_instant(reporter_id: int, reported_id: int) -> bool:
    key = f"{reporter_id}:{reported_id}"
    now_ts = time.time()

    if len(_recent_report_alert) > 1000:
        cutoff = now_ts - 120
        for k in list(_recent_report_alert.keys()):
            if _recent_report_alert[k] < cutoff:
                del _recent_report_alert[k]

    last = _recent_report_alert.get(key, 0)
    _recent_report_alert[key] = now_ts
    return (now_ts - last) > _REPORT_DEDUP_SECONDS


def _get_reports_collection():
    try:
        from database import db
        if db is None:
            return None
        return db["reports"]
    except Exception as e:
        logger.debug(f"reports collection unavailable: {e}")
        return None


async def ensure_report_indexes():
    col = _get_reports_collection()
    if col is None:
        return
    try:
        await col.create_index("at")
        await col.create_index("reported_id")
        await col.create_index("reporter_id")
        await col.create_index("notified")
        await col.create_index("reviewed")
        logger.info("✅ reports indexes ready")
    except Exception as e:
        logger.error(f"reports index creation failed: {e}")


async def _lookup_name(uid: int) -> str:
    try:
        u = users.get(uid)
        if u:
            return esc(u.get("name") or "—")
        from database import users_collection as _uc
        if _uc is not None:
            d = await _uc.find_one({"user_id": uid}, {"name": 1})
            if d:
                return esc(d.get("name") or "—")
    except Exception:
        pass
    return "—"


async def log_report(context, reporter_id: int, reported_id: int,
                     reason: str = None) -> bool:
    """Save report + INSTANT notify to OWNER + all admins."""
    col = _get_reports_collection()

    doc_id = None
    if col is not None:
        doc = {
            "reporter_id": reporter_id,
            "reported_id": reported_id,
            "reason": (reason or "")[:200],
            "at": utcnow(),
            "notified": False,
            "reviewed": False,
            "reviewed_by": None,
            "reviewed_at": None,
        }
        try:
            result = await col.insert_one(doc)
            doc_id = result.inserted_id
            analytics["reports_today"] += 1
            logger.info(f"📝 Report logged: {reporter_id} → {reported_id}")
        except Exception as e:
            logger.error(f"Report insert failed: {e}")

    targets = set()
    if OWNER_ID:
        targets.add(OWNER_ID)
    try:
        for a in admin_cache:
            if a:
                targets.add(a)
    except Exception:
        pass
    targets.discard(reporter_id)

    if not targets:
        logger.warning("No admins to notify for report")
        return True

    should_instant = _should_notify_instant(reporter_id, reported_id)
    if not should_instant:
        logger.info(
            f"🔕 Report {reporter_id}→{reported_id} logged but "
            f"instant skipped (dedup 60s)"
        )
        if col is not None and doc_id is not None:
            try:
                await col.update_one(
                    {"_id": doc_id},
                    {"$set": {"notified": True, "notified_at": utcnow(),
                              "deduped": True}},
                )
            except Exception:
                pass
        return True

    reporter_name = await _lookup_name(reporter_id)
    reported_name = await _lookup_name(reported_id)

    body = (
        f"🚨  ✨  <b>New Report</b>  ✨  🚨\n"
        f"▎\n"
        f"▎ 👤 <b>Reporter</b>\n"
        f"▎   ├ Name : {reporter_name}\n"
        f"▎   └ ID : <code>{reporter_id}</code>\n"
        f"▎\n"
        f"▎ 🎯 <b>Reported</b>\n"
        f"▎   ├ Name : {reported_name}\n"
        f"▎   └ ID : <code>{reported_id}</code>\n"
    )
    if reason:
        body += f"▎\n▎ 📝 <b>Reason</b>\n▎   └ {esc(reason)}\n"
    body += (
        f"▎\n"
        f"▎ 💡 <b>Quick Actions</b>\n"
        f"▎   ├ /userinfo {reported_id}\n"
        f"▎   ├ /ban {reported_id} reason\n"
        f"▎   └ /reports {reported_id}\n"
        f"▎\n"
        f"⏰ {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    notified_ok = 0
    if context is not None:
        for target in targets:
            try:
                res = await safe_send(context, target, body, parse_mode="HTML")
                if res:
                    notified_ok += 1
            except Exception as e:
                logger.debug(f"Report notify to {target} failed: {e}")

    if col is not None and doc_id is not None and notified_ok > 0:
        try:
            await col.update_one(
                {"_id": doc_id},
                {"$set": {"notified": True, "notified_at": utcnow(),
                          "notified_count": notified_ok}},
            )
        except Exception:
            pass

    logger.info(
        f"🚨 Report {reporter_id}→{reported_id} — "
        f"notified {notified_ok}/{len(targets)} admins instantly"
    )
    return True


async def send_report_digest(context):
    """SAFETY NET: retries reports that failed instant notification."""
    col = _get_reports_collection()
    if col is None:
        return

    try:
        pending = await col.find(
            {"notified": False}
        ).sort("at", 1).to_list(length=100)
    except Exception as e:
        logger.error(f"Report digest query failed: {e}")
        return

    if not pending:
        return

    logger.warning(
        f"⚠️ Report digest retry — {len(pending)} un-notified reports"
    )

    reported_counts = Counter(r["reported_id"] for r in pending)
    top_5 = reported_counts.most_common(5)

    lines = [
        "🚨  ✨  <b>Report Digest (Retry)</b>  ✨  🚨",
        "▎",
        f"▎ ⚠️ These reports failed instant delivery",
        f"▎ 📊 Count : <b>{len(pending)}</b>",
        "▎",
        "▎ 🔥  <b>Most Reported</b>",
    ]
    for reported_id, count in top_5:
        name = await _lookup_name(reported_id)
        lines.append(
            f"▎   • <code>{reported_id}</code> {name} — <b>{count}</b> reports"
        )
    lines.append("")
    lines.append("💡 Use /reports to view all")

    if OWNER_ID:
        try:
            await safe_send(
                context, OWNER_ID, "\n".join(lines), parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Digest retry send failed: {e}")

    try:
        ids = [r["_id"] for r in pending]
        await col.update_many(
            {"_id": {"$in": ids}},
            {"$set": {"notified": True, "notified_at": utcnow()}},
        )
    except Exception as e:
        logger.error(f"Mark notified failed: {e}")


async def get_recent_reports(limit: int = 30) -> list:
    col = _get_reports_collection()
    if col is None:
        return []
    try:
        return await col.find(
            {"reviewed": {"$ne": True}}
        ).sort("at", -1).limit(limit).to_list(length=limit)
    except Exception as e:
        logger.error(f"get_recent_reports failed: {e}")
        return []


async def get_reports_for_user(target_id: int, limit: int = 20) -> list:
    col = _get_reports_collection()
    if col is None:
        return []
    try:
        return await col.find(
            {"reported_id": target_id}
        ).sort("at", -1).limit(limit).to_list(length=limit)
    except Exception as e:
        logger.error(f"get_reports_for_user failed: {e}")
        return []


async def mark_user_reports_reviewed(target_id: int, by_admin: int) -> int:
    col = _get_reports_collection()
    if col is None:
        return 0
    try:
        result = await col.update_many(
            {"reported_id": target_id, "reviewed": {"$ne": True}},
            {"$set": {
                "reviewed": True,
                "reviewed_by": by_admin,
                "reviewed_at": utcnow(),
            }},
        )
        return result.modified_count
    except Exception as e:
        logger.error(f"mark_user_reports_reviewed failed: {e}")
        return 0