from telegram import Update
from telegram.ext import ContextTypes

from config import VIP_PLANS
from services.vip import activate_vip, notify_owner_purchase
from utils import box_card, safe_send, utcnow
import logging

logger = logging.getLogger("sparktalks")


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

    # ═══ Idempotency check — prevent double credit ═══
    charge_id = getattr(payment, "telegram_payment_charge_id", None)
    if charge_id:
        from database import processed_payments_collection
        if processed_payments_collection is not None:
            try:
                result = await processed_payments_collection.update_one(
                    {"charge_id": charge_id},
                    {"$setOnInsert": {
                        "charge_id": charge_id,
                        "user_id": uid,
                        "at": utcnow(),
                    }},
                    upsert=True,
                )
                if result.upserted_id is None:
                    # ⭐ Already processed — skip activation
                    logger.warning(
                        f"⚠️ Duplicate payment {charge_id} ignored for {uid}"
                    )
                    await safe_send(
                        context, uid,
                        "ℹ️  ✨  <b>Already Processed</b>  ✨  ℹ️\n"
                        "▎\n"
                        "▎ This payment was already credited.\n"
                        "▎ Your VIP is active.\n"
                        "▎\n"
                        "💡 <i>Use /myvip to check status.</i>",
                        parse_mode="HTML",
                    )
                    return
            except Exception as e:
                # ⭐ Fail-safe: if idempotency check fails, allow activation
                logger.error(f"Payment idempotency check failed: {e}")

    try:
        plan_key = payment.invoice_payload.split(":")[1]
    except Exception:
        return

    new_exp = await activate_vip(uid, plan_key)
    if not new_exp:
        await safe_send(
            context, uid,
            "⚠️ Payment received but activation failed.\n"
            "Admin has been notified. Please contact support.",
        )
        from config import OWNER_ID
        if OWNER_ID:
            await safe_send(
                context, OWNER_ID,
                f"⚠️ VIP activation FAILED for <code>{uid}</code>, plan <b>{plan_key}</b>.\n"
                f"💳 Charge ID: <code>{charge_id or 'N/A'}</code>",
                parse_mode="HTML",
            )
        return

    await notify_owner_purchase(context, update.effective_user, plan_key)
    plan = VIP_PLANS[plan_key]
    body = box_card("Welcome to VIP", [
        {"type": "text", "content": f"🎉 Hey {name}, payment successful!"},
        {"type": "divider"},
        {"type": "section", "emoji": "👑", "heading": "Your Plan"},
        {"type": "line", "content": f"🌟 {plan['name']}"},
        {"type": "line", "content": f"⌛ Until: {new_exp.strftime('%d %b %Y %H:%M')} UTC"},
        {"type": "divider"},
        {"type": "text", "content": "Thanks for supporting SparkTalks!"},
    ], emoji="🎉")
    await update.message.reply_text(body, parse_mode="HTML")