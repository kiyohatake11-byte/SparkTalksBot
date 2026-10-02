import html
import logging
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
# FANCY FONT HELPERS
# ──────────────────────────────────────────────────────────────
_BOLD_MAP = {
    'a': '𝐚', 'b': '𝐛', 'c': '𝐜', 'd': '𝐝', 'e': '𝐞', 'f': '𝐟',
    'g': '𝐠', 'h': '𝐡', 'i': '𝐢', 'j': '𝐣', 'k': '𝐤', 'l': '𝐥',
    'm': '𝐦', 'n': '𝐧', 'o': '𝐨', 'p': '𝐩', 'q': '𝐪', 'r': '𝐫',
    's': '𝐬', 't': '𝐭', 'u': '𝐮', 'v': '𝐯', 'w': '𝐰', 'x': '𝐱',
    'y': '𝐲', 'z': '𝐳',
    'A': '𝐀', 'B': '𝐁', 'C': '𝐂', 'D': '𝐃', 'E': '𝐄', 'F': '𝐅',
    'G': '𝐆', 'H': '𝐇', 'I': '𝐈', 'J': '𝐉', 'K': '𝐊', 'L': '𝐋',
    'M': '𝐌', 'N': '𝐍', 'O': '𝐎', 'P': '𝐏', 'Q': '𝐐', 'R': '𝐑',
    'S': '𝐒', 'T': '𝐓', 'U': '𝐔', 'V': '𝐕', 'W': '𝐖', 'X': '𝐗',
    'Y': '𝐘', 'Z': '𝐙',
    '0': '𝟎', '1': '𝟏', '2': '𝟐', '3': '𝟑', '4': '𝟒',
    '5': '𝟓', '6': '𝟔', '7': '𝟕', '8': '𝟖', '9': '𝟗',
}

_ITALIC_MAP = {
    'a': '𝑎', 'b': '𝑏', 'c': '𝑐', 'd': '𝑑', 'e': '𝑒', 'f': '𝑓',
    'g': '𝑔', 'h': 'ℎ', 'i': '𝑖', 'j': '𝑗', 'k': '𝑘', 'l': '𝑙',
    'm': '𝑚', 'n': '𝑛', 'o': '𝑜', 'p': '𝑝', 'q': '𝑞', 'r': '𝑟',
    's': '𝑠', 't': '𝑡', 'u': '𝑢', 'v': '𝑣', 'w': '𝑤', 'x': '𝑥',
    'y': '𝑦', 'z': '𝑧',
    'A': '𝐴', 'B': '𝐵', 'C': '𝐶', 'D': '𝐷', 'E': '𝐸', 'F': '𝐹',
    'G': '𝐺', 'H': '𝐻', 'I': '𝐼', 'J': '𝐽', 'K': '𝐾', 'L': '𝐿',
    'M': '𝑀', 'N': '𝑁', 'O': '𝑂', 'P': '𝑃', 'Q': '𝑄', 'R': '𝑅',
    'S': '𝑆', 'T': '𝑇', 'U': '𝑈', 'V': '𝑉', 'W': '𝑊', 'X': '𝑋',
    'Y': '𝑌', 'Z': '𝑍',
}


def to_bold(text: str) -> str:
    """Convert text to 𝐌𝐚𝐭𝐡 𝐁𝐨𝐥𝐝 style."""
    return "".join(_BOLD_MAP.get(c, c) for c in text)


def to_italic(text: str) -> str:
    """Convert text to 𝐼𝑡𝑎𝑙𝑖𝑐 style."""
    return "".join(_ITALIC_MAP.get(c, c) for c in text)


# ──────────────────────────────────────────────────────────────
# BOX CARD SYSTEM — AUTO WIDTH
# ──────────────────────────────────────────────────────────────
MIN_WIDTH = 26
MAX_WIDTH = 42
PAD = 3  # for "│ " prefix and trailing space


def _vis_len(text: str) -> int:
    """
    Approximate visible length. Bold Unicode chars count as 1.
    HTML tags removed for length calc.
    """
    # Strip HTML tags for length estimate
    import re
    clean = re.sub(r'<[^>]+>', '', text)
    return len(clean)


def _wrap(text: str, max_len: int) -> list:
    """Word-wrap a text into lines of max_len visible chars."""
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
    """Collect all displayable lines to compute max width."""
    lines = []

    # Title line
    title_vis = _vis_len(title) + 3  # emoji + 2 spaces
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
    """
    # Auto-calculate width
    if width is None:
        all_vis = _collect_all_lines(blocks, title, emoji)
        max_vis = max(all_vis) if all_vis else MIN_WIDTH
        width = max(MIN_WIDTH, min(max_vis + PAD, MAX_WIDTH))

    BOX_TOP_L = "╭" + "─" * width + "╮"
    BOX_MID_L = "├" + "─" * width + "┤"
    BOX_BOTTOM_L = "╰" + "─" * width + "╯"

    fancy_title = to_bold(title)
    lines = [f"{emoji}  {fancy_title}" if emoji else fancy_title]
    lines.append(BOX_MID_L)

    inner_width = width - 1  # space for "│ " prefix... actually "│ " = 2 chars, so inner = width - 2

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
            heading = to_bold(block["heading"])
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
            content = f'💡 "{block["content"]}"'
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


# Legacy compatibility
def spark_card(title: str, body: str, footer: str = None, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def card(title: str, body: str, emoji: str = "") -> str:
    return box_simple(title, body, emoji=emoji)


def section(heading: str, emoji: str = "") -> str:
    fancy = to_bold(heading)
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