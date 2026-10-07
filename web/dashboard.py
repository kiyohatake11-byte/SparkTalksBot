"""Flask analytics dashboard + anonymous WebRTC voice signaling."""
import os
import logging
import secrets
import traceback
from functools import wraps

from flask import Flask, render_template, request, Response, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

from config import (
    DASHBOARD_USER, DASHBOARD_PASS, DASHBOARD_SECRET_KEY,
)
from utils import utcnow

logger = logging.getLogger("sparktalks")

socketio: SocketIO = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        async_mode="eventlet",
        logger=False,
        engineio_logger=False,
    )

    # ─── Basic Auth helpers ───────────────────────────────────
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
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return authenticate()
            return f(*args, **kwargs)
        return wrapper

    # ─── Dashboard routes ─────────────────────────────────────
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
            "analytics": {k: (str(v) if k == "start_time" and v else v)
                          for k, v in analytics_ref.items()},
            "server_time": str(utcnow()),
        })

    @app.route("/admin/api/users")
    @requires_auth
    def api_users():
        users = sorted(
            users_ref.values(),
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

    # ─── Voice Room page ──────────────────────────────────────
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
            return (
                f"<h2>Voice Error</h2>"
                f"<pre>{str(e)}</pre>"
                f"<pre>{traceback.format_exc()}</pre>"
            ), 500

    # Temporary test route
    @app.route("/voice-test")
    def voice_test():
        try:
            return render_template("voice.html", room_id="test", token="test")
        except Exception as e:
            return f"<pre>{traceback.format_exc()}</pre>", 500

    # ─── Socket.IO signaling ──────────────────────────────────
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