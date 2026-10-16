"""Referral system — milestone rewards based on invite count."""
import secrets
import string
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    BOT_USERNAME, REFERRAL_REWARDS_NEEDED, REFERRAL_REWARD_DAYS,
    REFERRAL_MILESTONES,
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


# ══════════════════════════════════════════════════════════════
# MAIN REFERRAL HANDLER
# ══════════════════════════════════════════════════════════════

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

    total_refs = await users_collection.count_documents({"referred_by": referrer_id})

    analytics["referrals_today"] += 1

    # ⭐ Check & grant milestone rewards
    await _check_and_grant_milestones(context, referrer_id, total_refs)

    # ⭐ Progress notification
    await _notify_referral_progress(context, referrer_id, total_refs)

    return True


async def _check_and_grant_milestones(context, referrer_id: int, total_refs: int):
    """Grant any newly-unlocked milestone rewards."""
    referrer = await users_collection.find_one({"user_id": referrer_id})
    if not referrer:
        return

    claimed = referrer.get("referral_milestones_claimed") or []
    if not isinstance(claimed, list):
        claimed = []
    claimed_set = set(int(m) for m in claimed if str(m).isdigit())

    newly_claimed = []
    for milestone_count in sorted(REFERRAL_MILESTONES.keys()):
        if total_refs < milestone_count:
            continue
        if milestone_count in claimed_set:
            continue

        reward = REFERRAL_MILESTONES[milestone_count]
        ok = await _grant_milestone_reward(
            context, referrer_id, milestone_count, reward, total_refs
        )
        if ok:
            newly_claimed.append(milestone_count)

    if newly_claimed:
        await users_collection.update_one(
            {"user_id": referrer_id},
            {"$addToSet": {"referral_milestones_claimed": {"$each": newly_claimed}}}
        )
        ru = users.get(referrer_id)
        if ru:
            cl = ru.get("referral_milestones_claimed") or []
            for m in newly_claimed:
                if m not in cl:
                    cl.append(m)
            ru["referral_milestones_claimed"] = cl


async def _grant_milestone_reward(context, uid: int, milestone: int,
                                   reward: dict, total_refs: int) -> bool:
    """Grant a single milestone reward. Returns True on success."""
    rtype = reward.get("type")
    amount = int(reward.get("amount", 1))
    label = reward.get("label", "Reward")

    try:
        # ═══ Gender credits ═══
        if rtype == "gender_credits":
            await users_collection.update_one(
                {"user_id": uid},
                {"$inc": {"gender_pref_credits": amount}}
            )
            ru = users.get(uid)
            if ru:
                ru["gender_pref_credits"] = ru.get("gender_pref_credits", 0) + amount
                new_total = ru["gender_pref_credits"]
            else:
                new_total = amount

            try:
                from services.gifts import log_gift
                await log_gift(
                    context, uid,
                    gift_type="Gender Filter Credits",
                    gift_value=amount,
                    reason=f"Referral milestone {milestone} reached ({total_refs} refs)",
                    extra={"🎯 Total referrals": total_refs},
                )
            except Exception as e:
                logger.error(f"Gift log failed: {e}")

            await safe_send(
                context, uid,
                f"🎉  ✨  <b>Milestone Unlocked!</b>  ✨  🎉\n"
                f"▎\n"
                f"▎ 🎯 You reached <b>{milestone}</b> referrals!\n"
                f"▎\n"
                f"▎ 🎁 Reward : <b>{label}</b>\n"
                f"▎ ⚡ Total credits : <b>{new_total}</b>\n"
                f"▎\n"
                f"▎ 💡 Use it via 🚻 Search By Gender",
                parse_mode="HTML",
            )
            return True

        # ═══ VIP hours ═══
        elif rtype == "vip_hours":
            from services.vip import extend_vip_hours
            new_exp = await extend_vip_hours(
                uid, amount,
                reason=f"Referral milestone {milestone} ({total_refs} refs)",
                source="referral",
            )
            if new_exp:
                try:
                    from services.gifts import log_gift
                    await log_gift(
                        context, uid,
                        gift_type="VIP hours",
                        gift_value=amount,
                        reason=f"Referral milestone {milestone} reached ({total_refs} refs)",
                        extra={"⌛ VIP until": new_exp.strftime("%d %b %Y %H:%M")},
                    )
                except Exception as e:
                    logger.error(f"Gift log failed: {e}")

                await safe_send(
                    context, uid,
                    f"🎉  ✨  <b>Milestone Unlocked!</b>  ✨  🎉\n"
                    f"▎\n"
                    f"▎ 👑 You reached <b>{milestone}</b> referrals!\n"
                    f"▎\n"
                    f"▎ 🎁 Reward : <b>{label}</b>\n"
                    f"▎ ⌛ VIP until : <b>{new_exp.strftime('%d %b %Y %H:%M')}</b>\n"
                    f"▎\n"
                    f"▎ 💎 Enjoy your VIP perks!",
                    parse_mode="HTML",
                )
                return True

        # ═══ VIP days ═══
        elif rtype == "vip_days":
            from services.vip import extend_vip_days
            new_exp = await extend_vip_days(
                uid, amount,
                reason=f"Referral milestone {milestone} ({total_refs} refs)",
                source="referral",
            )
            if new_exp:
                try:
                    from services.gifts import log_gift
                    await log_gift(
                        context, uid,
                        gift_type="VIP days",
                        gift_value=amount,
                        reason=f"Referral milestone {milestone} reached ({total_refs} refs)",
                        extra={"⌛ VIP until": new_exp.strftime("%d %b %Y")},
                    )
                except Exception as e:
                    logger.error(f"Gift log failed: {e}")

                await safe_send(
                    context, uid,
                    f"🎉  ✨  <b>Milestone Unlocked!</b>  ✨  🎉\n"
                    f"▎\n"
                    f"▎ 👑 You reached <b>{milestone}</b> referrals!\n"
                    f"▎\n"
                    f"▎ 🎁 Reward : <b>{label}</b>\n"
                    f"▎ ⌛ VIP until : <b>{new_exp.strftime('%d %b %Y')}</b>\n"
                    f"▎\n"
                    f"▎ 💎 Enjoy your VIP perks!",
                    parse_mode="HTML",
                )
                return True

    except Exception as e:
        logger.error(f"Milestone {milestone} grant failed for {uid}: {e}", exc_info=True)

    return False


async def _notify_referral_progress(context, referrer_id: int, total_refs: int):
    """Send progress notification for the latest referral."""
    claimed = []
    ru = users.get(referrer_id)
    if ru:
        claimed = ru.get("referral_milestones_claimed") or []

    # Find next unclaimed milestone
    next_milestone = None
    for m in sorted(REFERRAL_MILESTONES.keys()):
        if m > total_refs and m not in claimed:
            next_milestone = m
            break

    if next_milestone is None:
        msg = (
            f"🎁  ✨  <b>New Referral!</b>  ✨  🎁\n"
            f"▎\n"
            f"▎ 👥 Total : <b>{total_refs}</b>\n"
            f"▎\n"
            f"▎ 💡 /invite to keep sharing!"
        )
    else:
        remaining = next_milestone - total_refs
        next_reward = REFERRAL_MILESTONES[next_milestone]
        msg = (
            f"🎁  ✨  <b>New Referral!</b>  ✨  🎁\n"
            f"▎\n"
            f"▎ 👥 Total : <b>{total_refs}</b>\n"
            f"▎ 🎯 Next reward : <b>{next_reward['label']}</b>\n"
            f"▎ ⏳ In <b>{remaining}</b> more referrals\n"
            f"▎\n"
            f"▎ 💡 /invite to share!"
        )

    try:
        await safe_send(context, referrer_id, msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Referral progress notify failed: {e}")


# ══════════════════════════════════════════════════════════════
# INVITE SCREEN
# ══════════════════════════════════════════════════════════════

async def send_invite(update: Update, context: ContextTypes.DEFAULT_TYPE, u: dict):
    uid = update.effective_user.id
    code = await ensure_referral_code(uid, u)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"

    count = 0
    if users_collection is not None:
        count = await users_collection.count_documents({"referred_by": uid})

    # Build milestone progress lines
    milestone_lines = []
    claimed = set(u.get("referral_milestones_claimed") or [])
    for m in sorted(REFERRAL_MILESTONES.keys()):
        reward = REFERRAL_MILESTONES[m]
        if m in claimed:
            status = "✅"
        elif count >= m:
            status = "⏳"
        else:
            status = "🔒"
        milestone_lines.append(
            f"▎   {status} {m} invites → {reward['label']}"
        )

    credits = u.get("gender_pref_credits", 0)

    text = (
        "🎁  ✨  <b>Invite & Earn</b>  ✨  🎁\n"
        "▎\n"
        "▎ 🎉 Share your link & earn rewards!\n"
        "▎\n"
        "▎ 🔗 <b>Your Invite Link</b>\n"
        f"▎   └ <code>{link}</code>\n"
        "▎\n"
        "▎ 📊 <b>Your Progress</b>\n"
        f"▎   ├ 👥 Referrals : <b>{count}</b>\n"
        f"▎   └ 🎯 Credits available : <b>{credits}</b>\n"
        "▎\n"
        "▎ 🎁 <b>Milestone Rewards</b>\n"
        + "\n".join(milestone_lines) + "\n"
        "▎\n"
        "💡 <i>Milestones unlock once per user!</i>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Share Link",
                             url=f"https://t.me/share/url?url={link}&text=Join%20SparkTalks!")
    ]])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")