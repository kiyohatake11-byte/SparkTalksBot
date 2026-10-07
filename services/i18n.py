"""Multi-language support."""
import logging
from config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

logger = logging.getLogger("sparktalks")

# Lazy import translation dicts
_TRANSLATIONS = None


def _load():
    global _TRANSLATIONS
    if _TRANSLATIONS is not None:
        return
    from locales import en, hi, ru, ar
    _TRANSLATIONS = {
        "en": en.STRINGS,
        "hi": hi.STRINGS,
        "ru": ru.STRINGS,
        "ar": ar.STRINGS,
    }


def _(key: str, lang: str = None, **kwargs) -> str:
    """Translate key. Falls back to English if missing."""
    _load()
    lang = lang or DEFAULT_LANGUAGE
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    text = _TRANSLATIONS.get(lang, {}).get(key)
    if text is None:
        text = _TRANSLATIONS["en"].get(key, key)
    try:
        return text.format(**kwargs) if kwargs else text
    except Exception:
        return text


def t(u: dict, key: str, **kwargs) -> str:
    """Shortcut using user dict."""
    lang = (u or {}).get("language") or DEFAULT_LANGUAGE
    return _(key, lang=lang, **kwargs)