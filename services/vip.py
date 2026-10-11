import asyncio
from datetime import timedelta
import logging

from telegram import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from config import VIP_PLANS, OWNER_ID
from state import users, analytics
from database import users_collection
from utils import box_card, safe_send, utcnow

logger = logging.getLogger("sparktalks")


async def send_vip_invoice(context, chat_id: int, plan_key: str):
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=plan["name"],
            description=(
                f"Get {plan['label']} of SparkTalks VIP · "
                f"{plan['price_inr']} / {plan['price_usd']} / {plan['stars']} Stars"
            ),
            payload=f"vip:{plan_key}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=plan["name"], amount=plan["stars"])],
            start_parameter=f"vip_{plan_key}",
        )
    except (Forbidden, BadRequest) as e:
        logger.warning(f"Invoice send failed for {chat_id}: {e}")
        await safe_send(
            context, chat_id,
            box_card("Payment Unavailable", [
                {"type": "text", "content": "⚠️ Could not open payment window."},
            ], emoji="⚠️"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Invoice error: {e}")


async def notify_owner_purchase(context, user, plan_key: str):
    if not OWNER_ID:
        return
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
    username = f"@{user.username}" if user.username else "No username"
    body = box_card("VIP Purchase", [
        {"type": "section", "emoji": "👤", "heading": "Customer"},
        {"type": "line", "content": f"👤 {user.first_name} ({username})"},
        {"type": "line", "content": f"🆔 <code>{user.id}</code>"},
        {"type": "divider"},
        {"type": "section", "emoji": "📦", "heading": "Order"},
        {"type": "line", "content": f"📦 {plan['name']}"},
        {"type": "line", "content": f"⏱ {plan['label']}"},
        {"type": "line", "content": f"💰 {plan['price_inr']} · {plan['price_usd']} · {plan['stars']}⭐"},
    ], emoji="💰")
    await safe_send(context, OWNER_ID, body, parse_mode="HTML")


async def activate_vip(uid: int, plan_key: str):
    plan = VIP_PLANS.get(plan_key)
    if not plan or users_collection is None:
        logger.error("activate_vip: invalid plan or DB down")
        return None
    days = plan["days"]
    tier = plan["name"]
    now = utcnow()

    for attempt in range(3):
        try:
            doc = await users_collection.find_one({"user_id": uid})
            current = doc.get("vip_expiry_date") if doc else None
            new_exp = (current + timedelta(days=days)) if (current and current > now) else (now + timedelta(days=days))

            history_entry = {
                "plan": plan_key, "tier": tier, "days": days,
                "price_inr": plan["price_inr"], "price_usd": plan["price_usd"],
                "stars": plan["stars"], "at": now.isoformat(),
            }

            await users_collection.update_one(
                {"user_id": uid},
                {
                    "$set": {
                        "is_vip": True, "vip_expiry_date": new_exp,
                        "vip_tier_name": tier, "user_id": uid,
                    },
                    "$push": {"payment_history": history_entry},
                },
                upsert=True,
            )
            u = users.get(uid)
            if u:
                u["is_vip"] = True
                u["vip_expiry_date"] = new_exp
                u["vip_tier_name"] = tier
                ph = u.get("payment_history") or []
                ph.append(history_entry)
                u["payment_history"] = ph[-20:]
            analytics["vip_purchases_today"] += 1
            return new_exp
        except Exception as e:
            logger.error(f"activate_vip attempt {attempt+1} failed: {e}")
            await asyncio.sleep(1)

    logger.critical(f"⚠️ VIP activation FAILED for {uid}, plan {plan_key}")
    return None


async def extend_vip_days(uid: int, days: int, reason: str = "Bonus"):
    """Extend VIP by N days (referral bonus)."""
    if users_collection is None:
        return None
    now = utcnow()
    doc = await users_collection.find_one({"user_id": uid})
    current = doc.get("vip_expiry_date") if doc else None
    base = current if (current and current > now) else now
    new_exp = base + timedelta(days=days)
    tier = (doc.get("vip_tier_name") if doc else None) or "🎁 Bonus VIP"

    await users_collection.update_one(
        {"user_id": uid},
        {"$set": {
            "is_vip": True, "vip_expiry_date": new_exp,
            "vip_tier_name": tier, "user_id": uid,
        }},
        upsert=True,
    )
    u = users.get(uid)
    if u:
        u["is_vip"] = True
        u["vip_expiry_date"] = new_exp
        if not u.get("vip_tier_name") or u["vip_tier_name"] == "None":
            u["vip_tier_name"] = tier
    return new_exp


async def check_expired_vips(context: ContextTypes.DEFAULT_TYPE):
    if users_collection is None:
        return
    now = utcnow()
    cursor = users_collection.find({"is_vip": True, "vip_expiry_date": {"$lt": now}})
    count = 0
    async for doc in cursor:
        uid = doc["user_id"]
        await users_collection.update_one(
            {"user_id": uid},
            {"$set": {
                "is_vip": False, "vip_expiry_date": None,
                "vip_tier_name": "None", "pref_gender": "Any",
            }},
        )
        u = users.get(uid)
        if u:
            u["is_vip"] = False
            u["vip_expiry_date"] = None
            u["vip_tier_name"] = "None"
            u["pref_gender"] = "Any"

        body = box_card("VIP Expired", [
            {"type": "text", "content": "⌛ Your VIP has expired."},
            {"type": "divider"},
            {"type": "section", "emoji": "🔄", "heading": "What changed"},
            {"type": "line", "content": "• Gender filter: Disabled"},
            {"type": "line", "content": "• Block feature: Disabled"},
            {"type": "line", "content": "• Preference reset to Any"},
            {"type": "divider"},
            {"type": "quote", "content": "Renew to keep your perks!"},
        ], emoji="⌛")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍️ Renew VIP", callback_data="BUY_STORE"),
        ]])
        await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")
        count += 1
        if count % 20 == 0:
            await asyncio.sleep(1.0)