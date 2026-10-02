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
# FANCY FONT HELPERS (Unicode Transformation)
# ──────────────────────────────────────────────────────────────

# Mathematical Sans-Serif Bold (best looking on Telegram)
_BOLD_MAP = {
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

# Mathematical Italic
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

# Small Caps (stylish, compact)
_SMALLCAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ',
    'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ',
    'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ',
    's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
    'y': 'ʏ', 'z': 'ᴢ',
}

# Double-Struck (fancy, gaming style)
_DOUBLESTRUCK_MAP = {
    'a': '𝕒', 'b': '𝕓', 'c': '𝕔', 'd': '𝕕', 'e': '𝕖', 'f': '𝕗',
    'g': '𝕘', 'h': '𝕙', 'i': '𝕚', 'j': '𝕛', 'k': '𝕜', 'l': '𝕝',
    'm': '𝕞', 'n': '𝕟', 'o': '𝕠', 'p': '𝕡', 'q': '𝕢', 'r': '𝕣',
    's': '𝕤', 't': '𝕥', 'u': '𝕦', 'v': '𝕧', 'w': '𝕨', 'x': '𝕩',
    'y': '𝕪', 'z': '𝕫',
    'A': '𝔸', 'B': '𝔹', 'C': 'ℂ', 'D': '𝔻', 'E': '𝔼', 'F': '𝔽',
    'G': '𝔾', 'H': 'ℍ', 'I': '𝕀', 'J': '𝕁', 'K': '𝕂', 'L': '𝕃',
    'M': '𝕄', 'N': 'ℕ', 'O': '𝕆', 'P': 'ℙ', 'Q': 'ℚ', 'R': 'ℝ',
    'S': '𝕊', 'T': '𝕋', 'U': '𝕌', 'V': '𝕍', 'W': '𝕎', 'X': '𝕏',
    'Y': '𝕐', 'Z': 'ℤ',
    '0': '𝟘', '1': '𝟙', '2': '𝟚', '3': '𝟛', '4': '𝟜',
    '5': '𝟝', '6': '𝟞', '7': '𝟟', '8': '𝟠', '9': '𝟡',
}


def to_bold(text: str) -> str:
    """Convert text to 𝗕𝗼𝗹𝗱 𝗦𝗮𝗻𝘀 style."""
    return "".join(_BOLD_MAP.get(c, c) for c in text)


def to_italic(text: str) -> str:
    """Convert text to 𝐼𝑡𝑎𝑙𝑖𝑐 style."""
    return "".join(_ITALIC_MAP.get(c, c) for c in text)


def to_smallcaps(text: str) -> str:
    """Convert text to sᴍᴀʟʟ ᴄᴀᴘs style."""
    return "".join(_SMALLCAPS_MAP.get(c, c) for c in text)


def to_doublestruck(text: str) -> str:
    """Convert text to 𝔻𝕠𝕦𝕓𝕝𝕖 𝕊𝕥𝕣𝕦𝕔𝕜 style."""
    return "".join(_DOUBLESTRUCK_MAP.get(c, c) for c in text)


def fancy_title(text: str) -> str:
    """Smart title formatter — bold + spacing style."""
    return to_bold(text)


def spark_card(title: str, body: str, footer: str = None) -> str:
    """Enhanced spark card with fancy title."""
    fancy = to_bold(title)
    card = "✨ 𝗦𝗽𝗮𝗿𝗸𝗧𝗮𝗹𝗸𝘀 ✨\n"
    card += "───────────────────────────────\n\n"
    card += f"❖ <b>{fancy}</b>\n\n"
    card += f"{body}\n"
    if footer:
        card += "\n───────────────────────────────\n"
        card += f"<i>❖ {footer}</i>"
    return card


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