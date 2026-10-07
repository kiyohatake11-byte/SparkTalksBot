"""Referral system."""
import secrets
import string
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import BOT_USERNAME, REFERRAL_REWARD_DAYS
from database import users_collection, save_user_to_db
from state import users, analytics
from utils import safe_send

logger = logging.getLogger("sparktalks")


def _generate_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


async def ensure_referral_code(uid: int, u: dict) -> str:
    code = u.get("referral_code")
    if code:
        return code
    for _ in range(5):
        candidate = _generate_code()
        if users_collection is None:
            u["referral_code"] = candidate
            await save_user_to_db(uid, u)
            return candidate
        exists = await users_collection.find_one({"referral_code": candidate})
        if not exists:
            u["referral_code"] = candidate
            await save_user_to_db(uid, u)
            return candidate
    code = f"U{uid % 100000:05d}"
    u["referral_code"] = code
    await save_user_to_db(uid, u)
    return code


async def handle_referral_payload(context, new_uid: int, payload: str) -> bool:
    if not payload.startswith("ref_"):
        return False
    code = payload[4:].strip().upper()
    if not code or users_collection is None:
        return False
    new_u = users.get(new_uid)
    if not new_u or new_u.get("referred_by"):
        return False
    referrer = await users_collection.find_one({"referral_code": code})
    if not referrer or referrer["user_id"] == new_uid:
        return False
    referrer_id = referrer["user_id"]
    new_u["referred_by"] = referrer_id
    await save_user_to_db(new_uid, new_u)

    from services.vip import extend_vip_days
    await extend_vip_days(referrer_id, REFERRAL_REWARD_DAYS, "Referral Bonus")
    analytics["referrals_today"] += 1

    try:
        await safe_send(
            context, referrer_id,
            f"🎁  ✨  <b>Referral Bonus!</b>  ✨  🎁\n"
            f"▎\n"
            f"▎ 🎉 Someone joined using your link!\n"
            f"▎\n"
            f"▎ 🎁 Bonus : <b>+{REFERRAL_REWARD_DAYS} days VIP</b>\n"
            f"▎\n"
            f"▎ 💡 /invite to share your link",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Referral notify failed: {e}")
    return True


async def send_invite(update: Update, context: ContextTypes.DEFAULT_TYPE, u: dict):
    uid = update.effective_user.id
    code = await ensure_referral_code(uid, u)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"

    count = 0
    if users_collection is not None:
        count = await users_collection.count_documents({"referred_by": uid})

    text = (
        "🎁  ✨  <b>Invite & Earn</b>  ✨  🎁\n"
        "▎\n"
        "▎ 🎉 Share your link & earn <b>free VIP days</b>!\n"
        "▎\n"
        "▎ 🔗 <b>Your Invite Link</b>\n"
        f"▎   └ <code>{link}</code>\n"
        "▎\n"
        "▎ 📋 <b>Details</b>\n"
        f"▎   ├ 👥 Referrals : <b>{count}</b>\n"
        f"▎   ├ 🎁 Reward : <b>+{REFERRAL_REWARD_DAYS} days VIP</b>\n"
        "▎   └ ♾️ Unlimited invites\n"
        "▎\n"
        "▎ 💡 <i>Share this link with friends!</i>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Share Link",
                             url=f"https://t.me/share/url?url={link}&text=Join%20SparkTalks!")
    ]])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")