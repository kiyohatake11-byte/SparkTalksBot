"""Analytics: stats, top users, recent, VIP list, queue, chats."""
import asyncio
import logging
import time
from telegram import Update
from telegram.ext import ContextTypes

from state import users, queue, analytics, admin_cache, muted_users, scheduled_broadcasts
import state
from database import is_owner_or_admin, users_collection, safe_count
from utils import esc
from handlers.admin._helpers import _sidebar, _deny

logger = logging.getLogger("sparktalks")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)

    t0 = time.monotonic()

    db_status = "✅ OK" if users_collection is not None else "❌ DOWN"

    try:
        total, vip, banned, admins = await asyncio.gather(
            safe_count(),
            safe_count({"is_vip": True}),
            safe_count({"is_banned": True}),
            safe_count({"is_admin": True}),
        )
    except Exception as e:
        logger.error(f"Stats DB counts failed: {e}")
        total = vip = banned = admins = 0

    snapshot = list(users.values())
    cached_count = len(snapshot)
    active_pairs = 0
    searching = 0
    for u in snapshot:
        st = u.get("state")
        if st == "CHAT" and u.get("partner"):
            active_pairs += 1
        elif st == "SEARCHING":
            searching += 1
    active_pairs //= 2

    queue_len = len(queue)
    maint = "🚧 ON" if state.maintenance_mode else "✅ OFF"

    try:
        from database import _pending_writes
        pending_writes = len(_pending_writes)
    except Exception:
        pending_writes = 0

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    await update.message.reply_text(_sidebar(
        "Stats", "📊",
        [
            "🗄️ <b>Database</b>",
            f"  └ Status : {db_status}",
            "",
            "👥 <b>Users (DB)</b>",
            f"  ├ Total : <b>{total}</b>",
            f"  ├ ⭐ VIP : <b>{vip}</b>",
            f"  ├ 🚫 Banned : <b>{banned}</b>",
            f"  └ 🛡️ Admins : <b>{admins}</b>",
            "",
            "🟢 <b>Live (Memory)</b>",
            f"  ├ 🧠 Cached : <b>{cached_count}</b>",
            f"  ├ 💬 Active chats : <b>{active_pairs}</b>",
            f"  ├ 🔍 Searching : <b>{searching}</b>",
            f"  ├ 📋 Queue : <b>{queue_len}</b>",
            f"  ├ 💾 Pending writes : <b>{pending_writes}</b>",
            f"  └ 🚧 Maintenance : {maint}",
            "",
            "📅 <b>Today</b>",
            f"  ├ 💞 Matches : {analytics['matches_today']}",
            f"  ├ 🚨 Reports : {analytics['reports_today']}",
            f"  ├ ⚠️ Violations : {analytics['violations_today']}",
            f"  ├ 🔇 Mutes : {analytics['mutes_today']}",
            f"  ├ 🎁 Referrals : {analytics['referrals_today']}",
            f"  ├ 🎙️ Voice rooms : {analytics['voice_rooms_today']}",
            f"  ├ 🔄 Reconnects : {analytics['reconnects_today']}",
            f"  ├ 💾 Saves : {analytics['saves_today']}",
            f"  ├ 📢 Broadcasts : {analytics['broadcasts_sent']}",
            f"  └ 💰 VIP purchases : {analytics['vip_purchases_today']}",
            "",
            f"⏱ <i>Query time: {elapsed_ms}ms</i>",
        ],
    ), parse_mode="HTML")


async def cmd_topusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    rank = 1
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    async for doc in users_collection.find(
        {"is_banned": False}, {"user_id": 1, "name": 1, "total_chats": 1}
    ).sort("total_chats", -1).limit(10):
        medal = medals.get(rank, f"{rank}.")
        lines.append(f"  {medal} {esc(doc.get('name') or '—')} — {doc.get('total_chats', 0)} chats")
        rank += 1
    if not lines:
        lines = ["No data."]
    await update.message.reply_text(
        _sidebar("Top Users", "🏆", lines), parse_mode="HTML"
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    async for doc in users_collection.find(
        {"is_banned": False}, {"user_id": 1, "name": 1, "joined_date": 1}
    ).sort("joined_date", -1).limit(10):
        name = esc(doc.get("name") or "—")
        jd = doc.get("joined_date")
        jd_str = jd.strftime("%d %b") if jd else "—"
        lines.append(f"  • <code>{doc['user_id']}</code> {name} ({jd_str})")
    if not lines:
        lines = ["No recent users."]
    await update.message.reply_text(
        _sidebar("Recent Users", "🆕", lines), parse_mode="HTML"
    )


async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    if users_collection is None:
        return
    lines = []
    count = 0
    async for doc in users_collection.find(
        {"is_vip": True},
        {"user_id": 1, "name": 1, "vip_tier_name": 1, "vip_expiry_date": 1},
    ).sort("vip_expiry_date", 1).limit(30):
        name = esc(doc.get("name") or "—")
        tier = esc(doc.get("vip_tier_name") or "VIP")
        exp = doc.get("vip_expiry_date")
        exp_str = exp.strftime("%d %b") if exp else "—"
        lines.append(f"  • <code>{doc['user_id']}</code> {name} · 👑 {tier} · ⌛ {exp_str}")
        count += 1
    if not lines:
        lines = ["✅ No VIP users yet."]
    await update.message.reply_text(
        _sidebar(f"VIP Users ({count})", "👑", lines), parse_mode="HTML"
    )


async def cmd_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    lines = [f"Queue size : {len(queue)}", ""]
    for i, uid_ in enumerate(list(queue)[:10]):
        u = users.get(uid_)
        name = esc(u.get("name") if u else "?")
        lines.append(f"  • <code>{uid_}</code> — {name}")
    await update.message.reply_text(
        _sidebar("Queue", "🔍", lines), parse_mode="HTML"
    )


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner_or_admin(update.effective_user.id):
        return await _deny(update)
    chats = []
    seen = set()
    for uid, u in list(users.items()):
        if u.get("state") == "CHAT" and u.get("partner") and uid not in seen:
            pid = u["partner"]
            seen.add(uid)
            seen.add(pid)
            chats.append(f"  • <code>{uid}</code> ↔ <code>{pid}</code>")
    if not chats:
        chats = ["✅ No active chats."]
    await update.message.reply_text(
        _sidebar(f"Active Chats ({len(chats)})", "💬", chats), parse_mode="HTML"
    )