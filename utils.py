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
# FANCY FONT HELPERS — ARRANGEMENT 3
# ──────────────────────────────────────────────────────────────

# 1. Serif Bold — for HEADERS (𝐓𝐡𝐢𝐬 𝐬𝐭𝐲𝐥𝐞)
_SERIF_BOLD_MAP = {
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

# 2. Sans Bold — for BODY (𝗧𝗵𝗶𝘀 𝘀𝘁𝘆𝗹𝗲)
_SANS_BOLD_MAP = {
    'a': '𝗮', 'b': '𝗯', 'c': '𝗰', 'd': '𝗱', 'e': '𝗲', 'f': '𝗳',
    'g': '𝗴', 'h': '𝗵', 'i': '𝗶', 'j': '𝗷', 'k': '𝗸', 'l': '𝗹',
    'm': '𝗺', 'n': '𝗻', 'o': '𝗼', 'p': '𝗽', 'q': '𝗾', 'r': '𝗿',
    's': '𝘀', 't': '𝘁', 'u': '𝘂', 'v': '𝘃', 'w': '𝘄', 'x': '𝘅',
    'y': '𝘆', 'z': '𝘇',
    'A': '𝗔', 'B': '𝗕', 'C': '𝗖', 'D': '𝗗', 'E': '𝗘', 'F': '𝗙',
    'G': '𝗚', 'H': '𝗛', 'I': '𝗜', 'J': '𝗝', 'K': '𝗞', 'L': '𝗟',
    'M': '𝗠', 'N': '𝗡', 'O': '𝗢', 'P': '𝗣', 'Q': '𝗤', 'R': '𝗥',
    'S': '𝗦', 'T': '𝗧', 'U': '𝗨', 'V': '𝗩', 'W': '𝗪', 'X': '𝗫',
    'Y': '𝗬', 'Z': '𝗭',
    '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯', '4': '𝟰',
    '5': '𝟱', '6': '𝟲', '7': '𝟳', '8': '𝟴', '9': '𝟵',
}

# 3. Sans Bold Italic — for QUOTES (𝙏𝙝𝙞𝙨 𝙞𝙩𝙖𝙡𝙞𝙘)
_SANS_BOLD_ITALIC_MAP = {
    'a': '𝙖', 'b': '𝙗', 'c': '𝙘', 'd': '𝙙', 'e': '𝙚', 'f': '𝙛',
    'g': '𝙜', 'h': '𝙝', 'i': '𝙞', 'j': '𝙟', 'k': '𝙠', 'l': '𝙡',
    'm': '𝙢', 'n': '𝙣', 'o': '𝙤', 'p': '𝙥', 'q': '𝙦', 'r': '𝙧',
    's': '𝙨', 't': '𝙩', 'u': '𝙪', 'v': '𝙫', 'w': '𝙬', 'x': '𝙭',
    'y': '𝙮', 'z': '𝙯',
    'A': '𝘼', 'B': '𝘽', 'C': '𝘾', 'D': '𝘿', 'E': '𝙀', 'F': '𝙁',
    'G': '𝙂', 'H': '𝙃', 'I': '𝙄', 'J': '𝙅', 'K': '𝙆', 'L': '𝙇',
    'M': '𝙈', 'N': '𝙉', 'O': '𝙊', 'P': '𝙋', 'Q': '𝙌', 'R': '𝙍',
    'S': '𝙎', 'T': '𝙏', 'U': '𝙐', 'V': '𝙑', 'W': '𝙒', 'X': '𝙓',
    'Y': '𝙔', 'Z': '𝙕',
    '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯', '4': '𝟰',
    '5': '𝟱', '6': '𝟲', '7': '𝟳', '8': '𝟴', '9': '𝟵',
}


def to_serif_bold(text: str) -> str:
    """Convert text to 𝐒𝐞𝐫𝐢𝐟 𝐁𝐨𝐥𝐝 (for headers)."""
    return "".join(_SERIF_BOLD_MAP.get(c, c) for c in text)


def to_sans_bold(text: str) -> str:
    """Convert text to 𝗦𝗮𝗻𝘀 𝗕𝗼𝗹𝗱 (for body)."""
    return "".join(_SANS_BOLD_MAP.get(c, c) for c in text)


def to_sans_bold_italic(text: str) -> str:
    """Convert text to 𝙎𝙖𝙣𝙨 𝘽𝙤𝙡𝙙 𝙄𝙩𝙖𝙡𝙞𝙘 (for quotes)."""
    return "".join(_SANS_BOLD_ITALIC_MAP.get(c, c) for c in text)


# Default alias — body text style
def to_bold(text: str) -> str:
    """Default: Sans Bold (body)."""
    return to_sans_bold(text)


# ──────────────────────────────────────────────────────────────
# BOX CARD SYSTEM — AUTO WIDTH
# ──────────────────────────────────────────────────────────────
MIN_WIDTH = 26
MAX_WIDTH = 42
PAD = 3


def _vis_len(text: str) -> int:
    import re
    clean = re.sub(r'<[^>]+>', '', text)
    return len(clean)


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
    """Generate box card. Title/Section = Serif Bold. Quote = Sans Bold Italic."""
    if width is None:
        all_vis = _collect_all_lines(blocks, title, emoji)
        max_vis = max(all_vis) if all_vis else MIN_WIDTH
        width = max(MIN_WIDTH, min(max_vis + PAD, MAX_WIDTH))

    BOX_TOP_L = "╭" + "─" * width + "╮"
    BOX_MID_L = "├" + "─" * width + "┤"
    BOX_BOTTOM_L = "╰" + "─" * width + "╯"

    # ✅ TITLE uses SERIF BOLD
    fancy_title = to_serif_bold(title)
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
            # ✅ SECTION HEADING uses SERIF BOLD
            heading = to_serif_bold(block["heading"])
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
            # ✅ QUOTE uses SANS BOLD ITALIC
            italic_text = to_sans_bold_italic(block["content"])
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
    return box_card(title, [{"type": "text", "content": content}], emoji=emoji)


def box_with_footer(title: str, blocks: list, footer_lines: list, emoji: str = "") -> str:
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
    # section heading uses SERIF BOLD
    fancy = to_serif_bold(heading)
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