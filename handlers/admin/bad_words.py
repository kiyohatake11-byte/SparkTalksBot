"""Bad words: ban list + mute list management."""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from database import (
    is_owner_or_admin, bad_words_collection, load_bad_words,
)
from utils import utcnow, esc
from handlers.admin._helpers import (
    _sidebar, _safe_reply, _log_action, _deny, _require_db,
)

logger = logging.getLogger("sparktalks")


async def cmd_banword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return

    if not context.args:
        return await update.message.reply_text(
            _sidebar("Auto-Ban Word", "🚫", [
                "📌 <b>Usage:</b>",
                "<code>/banword word</code>",
                "",
                "📝 <b>Examples:</b>",
                "  <code>/banword badword</code>",
                "  <code>/banword \"free vip here\"</code>",
                "",
                "⚡ User using this word → <b>permanent ban</b>",
                "",
                "💡 <i>Admins/owner are exempt.</i>",
            ]),
            parse_mode="HTML",
        )

    word = " ".join(context.args).strip().lower()
    if len(word) < 2:
        return await update.message.reply_text("⚠️ Word too short (min 2 chars).")
    if len(word) > 100:
        return await update.message.reply_text("⚠️ Word too long (max 100 chars).")

    try:
        existing = await bad_words_collection.find_one({"word": word})
    except Exception as e:
        logger.error(f"banword lookup failed: {e}")
        existing = None

    if existing and existing.get("action") == "mute":
        return await update.message.reply_text(
            _sidebar("Conflict", "⚠️", [
                f"⚠️ '<code>{esc(word)}</code>' already in <b>mute list</b>.",
                "",
                "💡 Remove first:",
                f"  <code>/removeword {esc(word)}</code>",
            ]),
            parse_mode="HTML",
        )

    try:
        await bad_words_collection.update_one(
            {"word": word},
            {"$set": {
                "word": word,
                "action": "ban",
                "duration": 0,
                "added_by": update.effective_user.id,
                "added_at": utcnow(),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"banword DB failed: {e}")
        return await update.message.reply_text(
            f"❌ DB error: <code>{esc(str(e)[:200])}</code>",
            parse_mode="HTML",
        )

    await load_bad_words()

    await update.message.reply_text(
        _sidebar("Ban Word Added", "🚫", [
            f"📝 Word : <code>{esc(word)}</code>",
            f"⚡ Action : <b>Permanent Ban</b>",
            "",
            "💡 Auto-triggers on any message containing it.",
            "⚠️ Admins/owner are exempt.",
        ]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"ban word '{word}'")


async def cmd_muteword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return

    if not context.args:
        return await update.message.reply_text(
            _sidebar("Auto-Mute Word", "🔇", [
                "📌 <b>Usage:</b>",
                "<code>/muteword word [minutes]</code>",
                "",
                "📝 <b>Examples:</b>",
                "  <code>/muteword spammyword</code>       → 5 min (default)",
                "  <code>/muteword spammyword 10</code>   → 10 min",
                "  <code>/muteword \"buy now\" 60</code>   → 60 min",
                "",
                "⏱ <b>Duration range:</b> 1 – 10080 minutes (7 days)",
                "",
                "⚡ User using this word → <b>temporary mute</b>",
                "",
                "💡 <i>Admins/owner are exempt.</i>",
            ]),
            parse_mode="HTML",
        )

    word = context.args[0].strip().lower()
    if len(word) < 2:
        return await update.message.reply_text("⚠️ Word too short (min 2 chars).")
    if len(word) > 100:
        return await update.message.reply_text("⚠️ Word too long (max 100 chars).")

    duration_min = 5
    if len(context.args) >= 2:
        try:
            duration_min = int(context.args[1])
            if duration_min < 1 or duration_min > 10080:
                raise ValueError
        except ValueError:
            return await update.message.reply_text(
                "⚠️ Duration must be 1-10080 minutes (1min to 7days)."
            )

    duration_sec = duration_min * 60

    try:
        existing = await bad_words_collection.find_one({"word": word})
    except Exception as e:
        logger.error(f"muteword lookup failed: {e}")
        existing = None

    if existing and existing.get("action") == "ban":
        return await update.message.reply_text(
            _sidebar("Conflict", "⚠️", [
                f"⚠️ '<code>{esc(word)}</code>' already in <b>ban list</b>.",
                "",
                "💡 Remove first:",
                f"  <code>/removeword {esc(word)}</code>",
            ]),
            parse_mode="HTML",
        )

    try:
        await bad_words_collection.update_one(
            {"word": word},
            {"$set": {
                "word": word,
                "action": "mute",
                "duration": duration_sec,
                "added_by": update.effective_user.id,
                "added_at": utcnow(),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"muteword DB failed: {e}")
        return await update.message.reply_text(
            f"❌ DB error: <code>{esc(str(e)[:200])}</code>",
            parse_mode="HTML",
        )

    await load_bad_words()

    await update.message.reply_text(
        _sidebar("Mute Word Added", "🔇", [
            f"📝 Word : <code>{esc(word)}</code>",
            f"⏱ Duration : <b>{duration_min} min</b>",
            "",
            "💡 Auto-triggers on any message containing it.",
            "⚠️ Admins/owner are exempt.",
        ]),
        parse_mode="HTML",
    )
    _log_action(
        update.effective_user.id,
        f"mute word '{word}' for {duration_min} min",
    )


async def cmd_removeword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return

    if not context.args:
        return await update.message.reply_text(
            "Usage: <code>/removeword word</code>\n\n"
            "💡 Removes from ban OR mute list.",
            parse_mode="HTML",
        )

    word = " ".join(context.args).strip().lower()

    try:
        existing = await bad_words_collection.find_one({"word": word})
    except Exception as e:
        logger.error(f"removeword lookup failed: {e}")
        existing = None

    if not existing:
        return await update.message.reply_text(
            _sidebar("Not Found", "🔍",
                     [f"⚠️ '<code>{esc(word)}</code>' not in any list."]),
            parse_mode="HTML",
        )

    was_action = existing.get("action", "ban")
    was_label = "🚫 Ban" if was_action == "ban" else "🔇 Mute"

    try:
        await bad_words_collection.delete_one({"word": word})
    except Exception as e:
        logger.error(f"removeword DB failed: {e}")
        return await update.message.reply_text(
            f"❌ DB error: <code>{esc(str(e)[:200])}</code>",
            parse_mode="HTML",
        )

    await load_bad_words()

    await update.message.reply_text(
        _sidebar("Removed", "✅", [
            f"✅ Removed '<code>{esc(word)}</code>'",
            f"📋 Was in : {was_label} list",
        ]),
        parse_mode="HTML",
    )
    _log_action(update.effective_user.id, f"removed word '{word}'")


async def cmd_wordlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if not await _require_db(update):
        return

    try:
        cursor = bad_words_collection.find({}).sort("added_at", -1)
        rows = [doc async for doc in cursor]
    except Exception as e:
        logger.error(f"wordlist DB failed: {e}")
        return await update.message.reply_text(
            f"❌ DB error: <code>{esc(str(e)[:200])}</code>",
            parse_mode="HTML",
        )

    ban_words = [r for r in rows if r.get("action") == "ban"]
    mute_words = [r for r in rows if r.get("action") == "mute"]

    if not ban_words and not mute_words:
        return await update.message.reply_text(
            _sidebar("Bad Words", "📝", [
                "📭 No words configured.",
                "",
                "💡 Add with:",
                "  <code>/banword word</code>",
                "  <code>/muteword word 10</code>",
            ]),
            parse_mode="HTML",
        )

    lines = [
        f"📊 Total : <b>{len(rows)}</b>",
        f"  ├ 🚫 Ban : <b>{len(ban_words)}</b>",
        f"  └ 🔇 Mute : <b>{len(mute_words)}</b>",
    ]

    if ban_words:
        lines.append("")
        lines.append(f"🚫  <b>Ban Words ({len(ban_words)})</b>")
        for i, r in enumerate(ban_words, 1):
            lines.append(f"  {i}. <code>{esc(r.get('word', ''))}</code>")

    if mute_words:
        lines.append("")
        lines.append(f"🔇  <b>Mute Words ({len(mute_words)})</b>")
        for i, r in enumerate(mute_words, 1):
            dur = int(r.get("duration", 0) or 0) // 60
            lines.append(
                f"  {i}. <code>{esc(r.get('word', ''))}</code> — {dur}m"
            )

    lines.append("")
    lines.append("💡 Remove: <code>/removeword word</code>")

    await _safe_reply(
        update,
        _sidebar("Bad Words List", "📝", lines),
    )