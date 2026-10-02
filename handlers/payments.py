from telegram import Update
from telegram.ext import ContextTypes

from config import VIP_PLANS
from services.vip import activate_vip
from utils import spark_card, to_bold


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

    body = (
        f"🎉 Hey {name}, payment successful!\n\n"
        f"❖ <b>{to_bold('Your Plan')}</b>\n"
        f"  🌟 <b>{plan['name']}</b>\n"
        f"  ⌛ Valid until: {new_exp.strftime('%d %b %Y %H:%M')} UTC\n\n"
        f"<i>Thanks for supporting SparkTalks!</i>"
    )

    await update.message.reply_text(
        spark_card("Welcome to VIP", body, "Enjoy the perks"),
        parse_mode="HTML"
    )