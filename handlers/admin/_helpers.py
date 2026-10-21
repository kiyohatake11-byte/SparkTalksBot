"""Shared helpers used across all admin sub-modules."""
import logging
from utils import esc, split_message
from database import users_collection

logger = logging.getLogger("sparktalks")


def _sidebar(title: str, emoji: str, lines: list, tip: str = None) -> str:
    parts = [f"{emoji}  ✨  <b>{title}</b>  ✨  {emoji}", "▎"]
    parts.extend(f"▎ {l}" if l else "▎" for l in lines)
    if tip:
        parts.append("▎")
        parts.append(f"💡 <i>{tip}</i>")
    return "\n".join(parts)


async def _safe_reply(update, text: str, reply_markup=None):
    """Send message with split_message support for >4096 char content."""
    parts = split_message(text, max_len=4000)
    for i, part in enumerate(parts):
        try:
            if i == 0:
                await update.message.reply_text(
                    part, reply_markup=reply_markup, parse_mode="HTML",
                )
            else:
                await update.message.reply_text(part, parse_mode="HTML")
        except Exception as e:
            logger.error(f"_safe_reply part {i} failed: {e}")


def _log_action(admin_id: int, action: str):
    from state import admin_logs
    from utils import utcnow
    admin_logs.append({
        "time": utcnow().strftime("%H:%M"),
        "admin": admin_id, "action": action,
    })


async def _deny(update):
    return await update.message.reply_text(
        _sidebar("Access Denied", "🔒", ["⛔ Unauthorized!"], "Admin only."),
        parse_mode="HTML",
    )


async def _require_db(update) -> bool:
    if users_collection is None:
        try:
            await update.message.reply_text(
                _sidebar("DB Down", "❌", ["⚠️ Database not connected."]),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False
    return True


async def _not_found(update, identifier):
    return await update.message.reply_text(
        _sidebar(
            "Not Found", "🔍",
            [f"User <code>{esc(str(identifier))}</code> not found."],
        ),
        parse_mode="HTML",
    )