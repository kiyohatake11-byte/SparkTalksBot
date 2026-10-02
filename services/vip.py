from datetime import timedelta
from telegram import LabeledPrice
from telegram.ext import ContextTypes

from config import VIP_PLANS, OWNER_ID
from state import users
from database import users_collection
from utils import spark_card, safe_send, utcnow


async def send_vip_invoice(context, chat_id: int, plan_key: str):
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=plan["name"],
        description=f"Get {plan['label']} of SparkTalks VIP · {plan['price_inr']} / {plan['price_usd']} / {plan['stars']} Stars",
        payload=f"vip:{plan_key}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["stars"])],
        start_parameter=f"vip_{plan_key}"
    )


async def notify_owner_purchase(context, user, plan_key: str):
    if not OWNER_ID:
        return
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        return
    username = f"@{user.username}" if user.username else "No username"
    text = spark_card(
        "VIP Interest",
        f"👤 <b>{user.first_name}</b> ({username})\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"📦 Plan: <b>{plan['name']}</b>\n"
        f"⏱ {plan['label']}\n"
        f"💰 {plan['price_inr']} · {plan['price_usd']} · {plan['stars']}⭐\n\n"
        f"<i>Started Stars payment.</i>",
        "Sales Alert"
    )
    await safe_send(context, OWNER_ID, text, parse_mode="HTML")


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
        {"$set": {"is_vip": True, "vip_expiry_date": new_exp, "vip_tier_name": tier, "user_id": uid}},
        upsert=True
    )
    u = users.get(uid)
    if u:
        u["is_vip"] = True
        u["vip_expiry_date"] = new_exp
        u["vip_tier_name"] = tier
    return new_exp


async def check_expired_vips(context: ContextTypes.DEFAULT_TYPE):
    now = utcnow()
    cursor = users_collection.find({"is_vip": True, "vip_expiry_date": {"$lt": now}})
    async for doc in cursor:
        uid = doc["user_id"]
        await users_collection.update_one(
            {"user_id": uid},
            {"$set": {"is_vip": False, "vip_expiry_date": None, "vip_tier_name": "None", "pref_gender": "Any"}}
        )
        u = users.get(uid)
        if u:
            u["is_vip"] = False
            u["vip_expiry_date"] = None
            u["vip_tier_name"] = "None"
            u["pref_gender"] = "Any"
        await safe_send(
            context, uid,
            spark_card("VIP Expired", "⌛ Your VIP has expired. Preference reset to 'Any'."),
            parse_mode="HTML"
        )
