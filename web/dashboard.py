"""Flask dashboard + WebRTC voice rooms + Telegram webhook receiver."""
import os
import time
import logging
import secrets
import traceback
import asyncio
from functools import wraps
from collections import defaultdict

from flask import Flask, render_template, request, Response, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

from config import (
    DASHBOARD_USER, DASHBOARD_PASS, DASHBOARD_SECRET_KEY,
    WEBHOOK_SECRET,
)
from utils import utcnow

logger = logging.getLogger("sparktalks")

socketio: SocketIO = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_AUTH_ATTEMPTS: dict = defaultdict(list)
_AUTH_WINDOW = 300
_AUTH_MAX_ATTEMPTS = 5

# ⭐ NEW: Global refs for webhook dispatch
_bot_app = None
_bot_loop = None


def set_bot_context(bot_app, bot_loop):
    """Called from bot.py after Application is started."""
    global _bot_app, _bot_loop
    _bot_app = bot_app
    _bot_loop = bot_loop
    logger.info("✅ Bot context registered for webhook")


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = _AUTH_ATTEMPTS[ip]
    attempts[:] = [t for t in attempts if now - t < _AUTH_WINDOW]
    if len(attempts) >= _AUTH_MAX_ATTEMPTS:
        return False
    attempts.append(now)
    return True


def create_dashboard_app(mongo_client, users_ref, admin_ref, analytics_ref):
    global socketio

    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )
    app.secret_key = DASHBOARD_SECRET_KEY

    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False,
    )

    def check_auth(username, password):
        return (
            secrets.compare_digest(username, DASHBOARD_USER)
            and secrets.compare_digest(password, DASHBOARD_PASS)
        )

    def authenticate():
        return Response(
            "Login required.", 401,
            {"WWW-Authenticate": 'Basic realm="SparkTalks Dashboard"'},
        )

    def requires_auth(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr or "unknown"
            if not _check_rate_limit(ip):
                return Response("Too many attempts. Try again later.", 429)
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return authenticate()
            return f(*args, **kwargs)
        return wrapper

    # ══════════════════════════════════════════════════════════════
    # ⭐ WEBHOOK RECEIVER — 40K+ users, zero-gap deploy
    # ══════════════════════════════════════════════════════════════
    @app.route("/tg-webhook", methods=["POST"])
    def telegram_webhook():
        if _bot_app is None or _bot_loop is None:
            logger.error("Webhook received but bot not ready")
            return "Bot not ready", 503

        # ⭐ Security: verify secret token
        if WEBHOOK_SECRET:
            header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if not header_secret or not secrets.compare_digest(header_secret, WEBHOOK_SECRET):
                logger.warning("Webhook: invalid secret token")
                return "Unauthorized", 401

        data = request.get_json(silent=True)
        if not data:
            return "Bad Request", 400

        try:
            from telegram import Update
            update = Update.de_json(data, _bot_app.bot)
            if update is None:
                return "Bad Update", 400

            # ⭐ Dispatch to PTB update queue (async, non-blocking)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    _bot_app.update_queue.put(update),
                    _bot_loop,
                )
                future.result(timeout=3.0)
            except Exception as e:
                logger.error(f"Webhook queue put failed: {e}")
                # Return 500 so Telegram retries
                return "Queue Error", 500

            return "OK", 200

        except Exception as e:
            logger.error(f"Webhook processing error: {e}", exc_info=True)
            return "Server Error", 500

    # ══════════════════════════════════════════════════════════════
    # HEALTH CHECK (Render uses this)
    # ══════════════════════════════════════════════════════════════
    @app.route("/health")
    def health():
        # DB check
        if mongo_client is None:
            return "DB_DOWN", 503
        try:
            if mongo_client.topology_description is None:
                return "DB_INIT", 503
        except Exception:
            return "DB_ERROR", 503

        # Bot check
        if _bot_app is None:
            return "BOT_NOT_READY", 503

        # Queue check (if overflow, don't send more)
        try:
            qsize = _bot_app.update_queue.qsize()
            if qsize > 2000:
                return f"QUEUE_FULL:{qsize}", 503
        except Exception:
            pass

        return "OK", 200

    @app.route("/")
    def home():
        return "SparkTalks is online."

    # ══════════════════════════════════════════════════════════════
    # ADMIN DASHBOARD
    # ══════════════════════════════════════════════════════════════
    @app.route("/admin")
    @requires_auth
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/admin/api/stats")
    @requires_auth
    def api_stats():
        return jsonify({
            "online_users": len(users_ref),
            "admins": len(admin_ref),
            "analytics": {
                k: (str(v) if k == "start_time" and v else v)
                for k, v in analytics_ref.items()
            },
            "server_time": str(utcnow()),
        })

    @app.route("/admin/api/users")
    @requires_auth
    def api_users():
        try:
            snapshot = list(users_ref.values())
        except Exception as e:
            logger.warning(f"Dashboard users snapshot failed: {e}")
            snapshot = []

        users = sorted(
            snapshot,
            key=lambda x: x.get("last_active") or utcnow(),
            reverse=True,
        )[:50]
        return jsonify([{
            "user_id": u.get("user_id"),
            "name": u.get("name"),
            "username": u.get("username"),
            "is_vip": u.get("is_vip"),
            "is_banned": u.get("is_banned"),
            "state": u.get("state"),
            "last_active": str(u.get("last_active") or ""),
            "total_matches": u.get("total_matches", 0),
        } for u in users])

    @app.route("/admin/api/health")
    @requires_auth
    def api_health():
        db_ok = mongo_client is not None
        return jsonify({
            "db": "up" if db_ok else "down",
            "cached_users": len(users_ref),
            "admins": len(admin_ref),
        })

    # ══════════════════════════════════════════════════════════════
    # VOICE ROOMS
    # ══════════════════════════════════════════════════════════════
    @app.route("/voice/<room_id>")
    def voice_room_page(room_id):
        try:
            token = request.args.get("token", "")
            from services.voice_rooms import get_room, validate_token, is_room_expired

            room = get_room(room_id)
            if not room:
                return "Room not found or expired. Use /voice again in bot.", 404
            if is_room_expired(room):
                return "This voice room has expired.", 410
            if not validate_token(room_id, token):
                return "Invalid or expired link.", 403

            return render_template("voice.html", room_id=room_id, token=token)
        except Exception as e:
            logger.error(f"Voice page error: {e}\n{traceback.format_exc()}")
            return f"<h2>Voice Error</h2><pre>{str(e)}</pre>", 500

    _room_sids: dict = {}

    @socketio.on("join")
    def on_join(data):
        from services.voice_rooms import get_room, validate_token, is_room_expired

        room_id = data.get("room_id")
        token = data.get("token")
        if not room_id or not token:
            emit("error", {"message": "Missing room or token"})
            return

        room = get_room(room_id)
        if not room or is_room_expired(room):
            emit("error", {"message": "Room expired or not found"})
            return
        uid = validate_token(room_id, token)
        if not uid:
            emit("error", {"message": "Invalid token"})
            return

        join_room(room_id)
        sids = _room_sids.setdefault(room_id, set())
        sids.add(request.sid)

        role = "initiator" if len(sids) == 1 else "joiner"
        emit("joined", {"role": role})

        if len(sids) == 2:
            emit("partner_joined", room=room_id, include_self=False)

        logger.info(f"Voice join: room={room_id} uid={uid} role={role} sids={len(sids)}")

    @socketio.on("signal")
    def on_signal(data):
        room_id = data.get("room_id")
        token = data.get("token")
        if not room_id:
            return
        from services.voice_rooms import validate_token
        if not validate_token(room_id, token):
            return

        payload = {"type": data.get("type")}
        if data.get("sdp"):
            payload["sdp"] = data["sdp"]
        if data.get("candidate"):
            payload["candidate"] = data["candidate"]

        emit(data.get("type"), payload, room=room_id, include_self=False)

    @socketio.on("leave")
    def on_leave(data):
        room_id = data.get("room_id")
        if room_id and room_id in _room_sids:
            _room_sids[room_id].discard(request.sid)
            leave_room(room_id)
            emit("partner_left", room=room_id, include_self=False)
            if not _room_sids[room_id]:
                _room_sids.pop(room_id, None)

    @socketio.on("disconnect")
    def on_disconnect():
        sid = request.sid
        for room_id, sids in list(_room_sids.items()):
            if sid in sids:
                sids.discard(sid)
                emit("partner_left", room=room_id, include_self=False)
                if not sids:
                    _room_sids.pop(room_id, None)
                break

    return app, socketio