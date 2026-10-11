"""Referral system — 20 refers = 1 day VIP."""
import secrets
import string
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    BOT_USERNAME, REFERRAL_REWARD_DAYS, REFERRAL_REWARDS_NEEDED,
)
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
    """New user joined via referral link."""
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

    # ⭐ Count TOTAL referrals from DB (accurate)
    total_refs = await users_collection.count_documents({"referred_by": referrer_id})

    analytics["referrals_today"] += 1

    # ⭐ Check if user hit a milestone (multiple of 20)
    just_hit_milestone = (total_refs % REFERRAL_REWARDS_NEEDED == 0)

    if just_hit_milestone:
        # Award 1 day VIP
        from services.vip import extend_vip_days
        await extend_vip_days(referrer_id, REFERRAL_REWARD_DAYS, "Referral Milestone")

        try:
            await safe_send(
                context, referrer_id,
                f"🎁  ✨  <b>Referral Milestone!</b>  ✨  🎁\n"
                f"▎\n"
                f"▎ 🎉 You reached <b>{total_refs}</b> referrals!\n"
                f"▎\n"
                f"▎ 🎁 Reward : <b>+{REFERRAL_REWARD_DAYS} day VIP</b>\n"
                f"▎\n"
                f"▎ 🎯 Next reward at <b>{total_refs + REFERRAL_REWARDS_NEEDED}</b> referrals\n"
                f"▎\n"
                f"▎ 💡 /invite to keep sharing!",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Referral milestone notify failed: {e}")
    else:
        # Progress notification to referrer
        remaining = REFERRAL_REWARDS_NEEDED - (total_refs % REFERRAL_REWARDS_NEEDED)
        try:
            await safe_send(
                context, referrer_id,
                f"🎁  ✨  <b>New Referral!</b>  ✨  🎁\n"
                f"▎\n"
                f"▎ 👥 Total : <b>{total_refs}</b>\n"
                f"▎ 🎯 Next reward in : <b>{remaining}</b> more referrals\n"
                f"▎\n"
                f"▎ 💡 /invite to share your link!",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Referral progress notify failed: {e}")

    return True


async def send_invite(update: Update, context: ContextTypes.DEFAULT_TYPE, u: dict):
    uid = update.effective_user.id
    code = await ensure_referral_code(uid, u)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"

    count = 0
    if users_collection is not None:
        count = await users_collection.count_documents({"referred_by": uid})

    # Progress calculation
    in_current_batch = count % REFERRAL_REWARDS_NEEDED
    remaining = REFERRAL_REWARDS_NEEDED - in_current_batch
    if in_current_batch == 0 and count > 0:
        remaining = REFERRAL_REWARDS_NEEDED  # just hit milestone
    total_earned = count // REFERRAL_REWARDS_NEEDED

    text = (
        "🎁  ✨  <b>Invite & Earn</b>  ✨  🎁\n"
        "▎\n"
        "▎ 🎉 Share your link & earn <b>free VIP days!</b>\n"
        "▎\n"
        "▎ 🔗 <b>Your Invite Link</b>\n"
        f"▎   └ <code>{link}</code>\n"
        "▎\n"
        "▎ 📋 <b>Details</b>\n"
        f"▎   ├ 👥 Referrals : <b>{count}</b>\n"
        f"▎   ├ 🎁 Earned : <b>{total_earned} days VIP</b>\n"
        f"▎   ├ 🎯 Progress : <b>{in_current_batch}/{REFERRAL_REWARDS_NEEDED}</b>\n"
        f"▎   └ ⏳ Next reward in <b>{remaining}</b> referrals\n"
        "▎\n"
        "▎ 💡 <i>20 referrals = 1 day VIP. Share your link!</i>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Share Link",
                             url=f"https://t.me/share/url?url={link}&text=Join%20SparkTalks!")
    ]])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")