import asyncio
from datetime import timedelta

from telegram import LabeledPrice
from telegram.ext import ContextTypes

from config import VIP_PLANS, OWNER_ID
from state import users
from database import users_collection
from utils import box_card, safe_send, utcnow


async def send_vip_invoice(context, chat_id: int, plan_key: str):
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
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


async def notify_owner_purchase(context, user, plan_key: str):
    if not OWNER_ID:
        return
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
    username = f"@{user.username}" if user.username else "No username"

    body = box_card(
        "VIP Interest",
        [
            {"type": "section", "emoji": "👤", "heading": "Customer"},
            {"type": "line", "content": f"👤 {user.first_name} ({username})"},
            {"type": "line", "content": f"🆔 <code>{user.id}</code>"},
            {"type": "divider"},
            {"type": "section", "emoji": "📦", "heading": "Order"},
            {"type": "line", "content": f"📦 Plan: <b>{plan['name']}</b>"},
            {"type": "line", "content": f"⏱ Duration: {plan['label']}"},
            {"type": "line", "content": f"💰 {plan['price_inr']} · {plan['price_usd']} · {plan['stars']}⭐"},
            {"type": "divider"},
            {"type": "text", "content": "Started Stars payment."},
        ],
        emoji="💰",
    )
    await safe_send(context, OWNER_ID, body, parse_mode="HTML")


async def activate_vip(uid: int, plan_key: str):
    plan = VIP_PLANS.get(plan_key)
    if not plan:
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
    """Find expired VIPs and downgrade them. Rate-limited."""
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
                {"type": "text", "content": "⌛ Your VIP has expired."},
                {"type": "divider"},
                {"type": "section", "emoji": "🔄", "heading": "What changed"},
                {"type": "line", "content": "• Gender filter: Disabled"},
                {"type": "line", "content": "• Preference reset to Any"},
                {"type": "divider"},
                {"type": "quote", "content": "Renew from /buy to keep your perks."},
            ],
            emoji="⌛",
        )
        await safe_send(context, uid, body, parse_mode="HTML")

        count += 1
        # ✅ Rate-limit: pause every 20 messages
        if count % 20 == 0:
            await asyncio.sleep(1.0)