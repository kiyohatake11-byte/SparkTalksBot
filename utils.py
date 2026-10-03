import html
import logging
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote
from telegram.ext import ContextTypes

from config import OWNER_ID, OWNER_USERNAME

logger = logging.getLogger("sparktalks")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_owner_link(prefill_text: str = None) -> str:
    if OWNER_USERNAME:
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", OWNER_USERNAME):
            logger.warning(f"Invalid OWNER_USERNAME format: {OWNER_USERNAME!r}")
        else:
            base = f"https://t.me/{OWNER_USERNAME}"
            return f"{base}?text={quote(prefill_text)}" if prefill_text else base
    if OWNER_ID and OWNER_ID != 0:
        return f"tg://user?id={OWNER_ID}"
    return "https://t.me/your_telegram_username"


# ──────────────────────────────────────────────────────────────
# HTML FORMATTING HELPERS
# ──────────────────────────────────────────────────────────────

def to_bold(text: str) -> str:
    return f"<b>{text}</b>"


def to_serif_bold(text: str) -> str:
    return f"<b>{text}</b>"


def to_sans_bold(text: str) -> str:
    return f"<b>{text}</b>"


def to_italic(text: str) -> str:
    return f"<i>{text}</i>"


def to_sans_bold_italic(text: str) -> str:
    return f"<b><i>{text}</i></b>"


def to_underline(text: str) -> str:
    return f"<u>{text}</u>"


def to_code(text: str) -> str:
    return f"<code>{text}</code>"


# ──────────────────────────────────────────────────────────────
# COMMAND SAFETY HELPER
# ──────────────────────────────────────────────────────────────
_COMMAND_RE = re.compile(r'(/[a-zA-Z0-9_@]+)')


def safe_italic(text: str) -> str:
    parts = _COMMAND_RE.split(text)
    rebuilt = []
    for part in parts:
        if part.startswith("/") and _COMMAND_RE.fullmatch(part):
            rebuilt.append(part)
        else:
            rebuilt.append(f"<b><i>{part}</i></b>")
    return "".join(rebuilt)


# ──────────────────────────────────────────────────────────────
# BOX CARD SYSTEM — AUTO WIDTH + EMOJI WIDTH AWARENESS
# ──────────────────────────────────────────────────────────────
MIN_WIDTH = 26
MAX_WIDTH = 42
PAD = 3

_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),  # Misc Symbols and Pictographs → Supplemental
    (0x2600, 0x27BF),    # Misc symbols, Dingbats
    (0x1F000, 0x1F2FF),  # Mahjong, Dominoes, Cards
    (0x2B00, 0x2BFF),    # Misc symbols and arrows
)


def _char_width(c: str) -> int:
    """Approximate visual width — emojis + CJK = 2, else 1."""
    cp = ord(c)
    for start, end in _EMOJI_RANGES:
        if start <= cp <= end:
            return 2
    if unicodedata.east_asian_width(c) in ('W', 'F'):
        return 2
    return 1


def _vis_len(text: str) -> int:
    clean = re.sub(r'<[^>]+>', '', text)
    return sum(_char_width(c) for c in clean)


def _wrap(text: str, max_len: int) -> list:
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
    lines = [_vis_len(title) + 3]
    for block in blocks:
        btype = block.get("type")
        if btype == "divider":
            continue
        elif btype == "text":
            for line in block["content"].split("\n"):
                lines.append(_vis_len(line.strip()) if line.strip() else 0)
        elif btype == "section":
            em = block.get("emoji", "")
            vis = _vis_len(block["heading"]) + (2 if em else 0)
            lines.append(vis)
        elif btype == "line":
            lines.append(_vis_len(block["content"]))
        elif btype == "quote":
            lines.append(_vis_len(f'💡 "{block["content"]}"'))
        elif btype == "kv":
            for key, val in block["items"]:
                lines.append(_vis_len(f"{key} : {val}"))
    return lines


def box_card(title: str, blocks: list, emoji: str = "", width: int = None) -> str:
    if width is None:
        all_vis = _collect_all_lines(blocks, title, emoji)
        max_vis = max(all_vis) if all_vis else MIN_WIDTH
        width = max(MIN_WIDTH, min(max_vis + PAD, MAX_WIDTH))

    BOX_MID_L = "├" + "─" * width + "┤"
    BOX_BOTTOM_L = "╰" + "─" * width + "╯"

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
                    for w in _wrap(line, width - 2):
                        lines.append(f"│ {w}")

        elif btype == "section":
            em = block.get("emoji", "")
            heading = f"<b>{block['heading']}</b>"
            prefix = f"{em} " if em else ""
            lines.append(f"│ {prefix}{heading}")

        elif btype == "line":
            wrapped = _wrap(block["content"], width - 2)
            for i, w in enumerate(wrapped):
                lines.append(f"│ {w}" if i == 0 else f"│   {w}")

        elif btype == "quote":
            italic_text = safe_italic(block["content"])
            content = f'💡 "{italic_text}"'
            for i, w in enumerate(_wrap(content, width - 2)):
                lines.append(f"│ {w}" if i == 0 else f"│    {w}")

        elif btype == "kv":
            for key, val in block["items"]:
                line = f"{key} : {val}"
                for i, w in enumerate(_wrap(line, width - 2)):
                    lines.append(f"│ {w}" if i == 0 else f"│   {w}")

    lines.append(BOX_BOTTOM_L)
    return "\n".join(lines)


def box_simple(title: str, content: str, emoji: str = "") -> str:
    return box_card(title, [{"type": "text", "content": content}], emoji=emoji)


def box_with_footer(title: str, blocks: list, footer_lines: list, emoji: str = "") -> str:
    box = box_card(title, blocks, emoji=emoji)
    if footer_lines:
        box += "\n\n" + "\n".join(footer_lines)
    return box


# Legacy aliases
def spark_card(title: str, body: str, footer: str = None, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def card(title: str, body: str, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def section(heading: str, emoji: str = "") -> str:
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