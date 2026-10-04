from telegram import Update
from telegram.ext import ContextTypes

from config import VIP_PLANS
from services.vip import activate_vip, notify_owner_purchase
from utils import box_card


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    parts = payload.split(":")
    if len(parts) < 2 or parts[0] != "vip" or parts[1] not in VIP_PLANS:
        await query.answer(ok=False, error_message="Invalid invoice. Please try again.")
        return
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    uid = update.effective_user.id
    name = update.effective_user.first_name or "there"
    try:
        plan_key = payment.invoice_payload.split(":")[1]
    except Exception:
        return
    new_exp = await activate_vip(uid, plan_key)
    if not new_exp:
        return
    plan = VIP_PLANS[plan_key]

    await notify_owner_purchase(context, update.effective_user, plan_key)

    body = box_card(
        "Welcome to VIP",
        [
            {"type": "text", "content": f"\U0001F389 Hey {name}, payment successful!"},
            {"type": "divider"},
            {"type": "section", "emoji": "\U0001F451", "heading": "Your Plan"},
            {"type": "line", "content": f"\U0001F31F {plan['name']}"},
            {"type": "line", "content": f"\u231B Until: {new_exp.strftime('%d %b %Y %H:%M')} UTC"},
            {"type": "divider"},
            {"type": "text", "content": "Thanks for supporting SparkTalks!"},
        ],
        emoji="\U0001F389",
    )
    await update.message.reply_text(body, parse_mode="HTML")
