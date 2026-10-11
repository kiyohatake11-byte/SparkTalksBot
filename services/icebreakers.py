"""Icebreaker questions — sent when two users connect."""
import random
import logging
from telegram.ext import ContextTypes

from config import ANTI_LINK_ENABLED
from utils import safe_send

logger = logging.getLogger("sparktalks")


ICEBREAKERS = [
    "If you could teleport anywhere right now, where would you go? ✈️",
    "What's the last song you listened to? 🎵",
    "Coffee or tea? ☕🍵",
    "What's your go-to comfort food? 🍕",
    "Describe your perfect Sunday in 5 words. 🌤️",
    "What's a movie you can rewatch forever? 🎬",
    "Sunrise or sunset person? 🌅🌇",
    "What's one thing on your bucket list? 📝",
    "If you had a superpower, what would it be? 🦸",
    "Dogs or cats? 🐶🐱",
    "What's the best trip you've ever taken? 🗺️",
    "Which fictional world would you live in? 🪐",
    "What's a hobby you've always wanted to try? 🎨",
    "Beach vacation or mountain cabin? 🏖️🏔️",
    "What's your favorite time of day? ⏰",
    "Sweet or savory breakfast? 🥞🥓",
    "What's a book that changed your perspective? 📚",
    "If you could have dinner with anyone, who? 🍽️",
    "What's the best advice you've ever received? 💭",
    "Star gazing or city lights? ✨🌃",
]


async def send_icebreaker(context: ContextTypes.DEFAULT_TYPE, uid: int):
    """Send a random icebreaker question to a user."""
    try:
        q = random.choice(ICEBREAKERS)
        await safe_send(
            context, uid,
            f"💡  ✨  <b>Icebreaker</b>  ✨  💡\n"
            f"▎\n"
            f"▎ 💬 {q}\n"
            f"▎\n"
            f"▎ <i>Not sure what to say? Try this!</i>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.debug(f"Icebreaker failed for {uid}: {e}")