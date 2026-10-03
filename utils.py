import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote
from telegram.ext import ContextTypes

from config import OWNER_ID, OWNER_USERNAME

logger = logging.getLogger("sparktalks")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_owner_link(prefill_text: str = None) -> str:
    if OWNER_USERNAME:
        base = f"https://t.me/{OWNER_USERNAME}"
        return f"{base}?text={quote(prefill_text)}" if prefill_text else base
    if OWNER_ID and OWNER_ID != 0:
        return f"tg://user?id={OWNER_ID}"
    return "https://t.me/your_telegram_username"


# ──────────────────────────────────────────────────────────────
# HTML FORMATTING HELPERS (Universal — works on all devices)
# ──────────────────────────────────────────────────────────────

def to_bold(text: str) -> str:
    """HTML bold — works perfectly on all devices."""
    return f"<b>{text}</b>"


def to_serif_bold(text: str) -> str:
    """HTML bold (for headers) — universal."""
    return f"<b>{text}</b>"


def to_sans_bold(text: str) -> str:
    """HTML bold — universal."""
    return f"<b>{text}</b>"


def to_italic(text: str) -> str:
    """HTML italic — universal."""
    return f"<i>{text}</i>"


def to_sans_bold_italic(text: str) -> str:
    """HTML bold + italic (for quotes)."""
    return f"<b><i>{text}</i></b>"


def to_underline(text: str) -> str:
    """HTML underline."""
    return f"<u>{text}</u>"


def to_code(text: str) -> str:
    """HTML monospace code."""
    return f"<code>{text}</code>"


# ──────────────────────────────────────────────────────────────
# COMMAND SAFETY HELPER
# ──────────────────────────────────────────────────────────────
_COMMAND_RE = re.compile(r'(/[a-zA-Z0-9_@]+)')


def safe_italic(text: str) -> str:
    """
    Convert text to HTML bold+italic BUT keep /commands plain
    so Telegram can detect them as clickable commands.
    """
    parts = _COMMAND_RE.split(text)
    rebuilt = []
    for part in parts:
        if part.startswith("/") and _COMMAND_RE.fullmatch(part):
            rebuilt.append(part)  # Command — plain
        else:
            rebuilt.append(f"<b><i>{part}</i></b>")
    return "".join(rebuilt)


# ──────────────────────────────────────────────────────────────
# BOX CARD SYSTEM — AUTO WIDTH
# ──────────────────────────────────────────────────────────────
MIN_WIDTH = 26
MAX_WIDTH = 42
PAD = 3


def _vis_len(text: str) -> int:
    """Visible length after stripping HTML tags."""
    clean = re.sub(r'<[^>]+>', '', text)
    return len(clean)


def _wrap(text: str, max_len: int) -> list:
    """Word-wrap a text into lines of max visible chars."""
    words = text.split()
    lines, current = [], ""
    current_vis = 0
    for word in words:
        word_vis = _vis_len(word)
        space_cost = 1 if current else 0
        if current_vis + word_vis + space_cost <= max_len:
            current = f"{current} {word}".strip() if current else word
            current_vis += word_vis + space_cost
        else:
            if current:
                lines.append(current)
            current = word
            current_vis = word_vis
    if current:
        lines.append(current)
    return lines or [""]


def _collect_all_lines(blocks: list, title: str, emoji: str) -> list:
    lines = []
    title_vis = _vis_len(title) + 3
    lines.append(title_vis)

    for block in blocks:
        btype = block.get("type")
        if btype == "divider":
            continue
        elif btype == "text":
            for line in block["content"].split("\n"):
                lines.append(_vis_len(line.strip()) if line.strip() else 0)
        elif btype == "section":
            em = block.get("emoji", "")
            heading = block["heading"]
            vis = len(heading) + (2 if em else 0)
            lines.append(vis)
        elif btype == "line":
            lines.append(_vis_len(block["content"]))
        elif btype == "quote":
            vis = _vis_len(f'💡 "{block["content"]}"')
            lines.append(vis)
        elif btype == "kv":
            for key, val in block["items"]:
                vis = _vis_len(f"{key} : {val}")
                lines.append(vis)
    return lines


def box_card(title: str, blocks: list, emoji: str = "", width: int = None) -> str:
    """
    Generate a box-style card with AUTO width.
    Uses HTML formatting — works perfectly on all devices.
    """
    if width is None:
        all_vis = _collect_all_lines(blocks, title, emoji)
        max_vis = max(all_vis) if all_vis else MIN_WIDTH
        width = max(MIN_WIDTH, min(max_vis + PAD, MAX_WIDTH))

    BOX_TOP_L = "╭" + "─" * width + "╮"
    BOX_MID_L = "├" + "─" * width + "┤"
    BOX_BOTTOM_L = "╰" + "─" * width + "╯"

    # ✅ TITLE uses HTML bold
    fancy_title = f"<b>{title}</b>"
    lines = [f"{emoji}  {fancy_title}" if emoji else fancy_title]
    lines.append(BOX_MID_L)

    for block in blocks:
        btype = block.get("type")

        if btype == "divider":
            lines.append(BOX_MID_L)

        elif btype == "text":
            for line in block["content"].split("\n"):
                if not line.strip():
                    lines.append("│")
                else:
                    wrapped = _wrap(line, width - 2)
                    for w in wrapped:
                        lines.append(f"│ {w}")

        elif btype == "section":
            em = block.get("emoji", "")
            # ✅ SECTION HEADING uses HTML bold
            heading = f"<b>{block['heading']}</b>"
            prefix = f"{em} " if em else ""
            lines.append(f"│ {prefix}{heading}")

        elif btype == "line":
            wrapped = _wrap(block["content"], width - 2)
            for i, w in enumerate(wrapped):
                if i == 0:
                    lines.append(f"│ {w}")
                else:
                    lines.append(f"│   {w}")

        elif btype == "quote":
            # ✅ QUOTE uses HTML bold+italic (commands stay plain)
            italic_text = safe_italic(block["content"])
            content = f'💡 "{italic_text}"'
            wrapped = _wrap(content, width - 2)
            for i, w in enumerate(wrapped):
                if i == 0:
                    lines.append(f"│ {w}")
                else:
                    lines.append(f"│    {w}")

        elif btype == "kv":
            for key, val in block["items"]:
                line = f"{key} : {val}"
                wrapped = _wrap(line, width - 2)
                for i, w in enumerate(wrapped):
                    if i == 0:
                        lines.append(f"│ {w}")
                    else:
                        lines.append(f"│   {w}")

    lines.append(BOX_BOTTOM_L)
    return "\n".join(lines)


def box_simple(title: str, content: str, emoji: str = "") -> str:
    """Simple box card with one text block."""
    return box_card(title, [{"type": "text", "content": content}], emoji=emoji)


def box_with_footer(title: str, blocks: list, footer_lines: list, emoji: str = "") -> str:
    """Box card with footer lines OUTSIDE the box."""
    box = box_card(title, blocks, emoji=emoji)
    if footer_lines:
        box += "\n\n" + "\n".join(footer_lines)
    return box


# Legacy compatibility aliases
def spark_card(title: str, body: str, footer: str = None, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def card(title: str, body: str, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def section(heading: str, emoji: str = "") -> str:
    """Section header with HTML bold."""
    fancy = f"<b>{heading}</b>"
    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}{fancy}"


def split_message(text: str, max_len: int = 4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    return parts


async def safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs):
    try:
        return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        logger.error(f"Send failed to {chat_id}: {e}")
        return None