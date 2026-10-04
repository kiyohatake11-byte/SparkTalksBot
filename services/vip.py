import asyncio
from datetime import timedelta

from telegram import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from config import VIP_PLANS, OWNER_ID
from state import users
from database import users_collection
from utils import box_card, safe_send, utcnow
import logging

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
                f"Get {plan['label']} of SparkTalks VIP \u00B7 "
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
                {"type": "text", "content": "\u26A0\uFE0F Could not open payment window."},
                {"type": "text", "content": "Please try again in a moment."},
            ], emoji="\u26A0\uFE0F"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Unexpected invoice error: {e}")


async def notify_owner_purchase(context, user, plan_key: str):
    if not OWNER_ID:
        return
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
    username = f"@{user.username}" if user.username else "No username"

    body = box_card(
        "VIP Purchase",
        [
            {"type": "section", "emoji": "\U0001F464", "heading": "Customer"},
            {"type": "line", "content": f"\U0001F464 {user.first_name} ({username})"},
            {"type": "line", "content": f"\U0001F194 <code>{user.id}</code>"},
            {"type": "divider"},
            {"type": "section", "emoji": "\U0001F4E6", "heading": "Order"},
            {"type": "line", "content": f"\U0001F4E6 Plan: <b>{plan['name']}</b>"},
            {"type": "line", "content": f"\u23F1 Duration: {plan['label']}"},
            {"type": "line", "content": f"\U0001F4B0 {plan['price_inr']} \u00B7 {plan['price_usd']} \u00B7 {plan['stars']}\u2B50"},
            {"type": "divider"},
            {"type": "text", "content": "\u2705 Payment successful (Stars)."},
        ],
        emoji="\U0001F4B0",
    )
    await safe_send(context, OWNER_ID, body, parse_mode="HTML")


async def activate_vip(uid: int, plan_key: str):
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return None
    if users_collection is None:
        logger.error("activate_vip: MongoDB not connected")
        return None
    days = plan["days"]
    tier = plan["name"]
    now = utcnow()
    doc = await users_collection.find_one({"user_id": uid})
    current = doc.get("vip_expiry_date") if doc else None
    new_exp = (current + timedelta(days=days)) if (current and current > now) else (now + timedelta(days=days))
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

        body = box_card(
            "VIP Expired",
            [
                {"type": "text", "content": "\u231B Your VIP has expired."},
                {"type": "divider"},
                {"type": "section", "emoji": "\U0001F504", "heading": "What changed"},
                {"type": "line", "content": "\u2022 Gender filter: Disabled"},
                {"type": "line", "content": "\u2022 Block feature: Disabled"},
                {"type": "line", "content": "\u2022 Preference reset to Any"},
                {"type": "divider"},
                {"type": "quote", "content": "Renew to keep your perks!"},
            ],
            emoji="\u231B",
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("\U0001F6CD\uFE0F Renew VIP", callback_data="BUY_STORE"),
        ]])
        await safe_send(context, uid, body, reply_markup=kb, parse_mode="HTML")

        count += 1
        if count % 20 == 0:
            await asyncio.sleep(1.0)
