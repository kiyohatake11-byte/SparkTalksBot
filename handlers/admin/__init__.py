"""Admin handlers package — re-exports all commands.

This allows `from handlers.admin import cmd_xxx` to work
exactly as it did before the split.
"""

# Helpers (rarely used externally, but re-exported for compatibility)
from handlers.admin._helpers import (
    _sidebar, _safe_reply, _log_action, _deny, _require_db, _not_found,
)

# User management
from handlers.admin.user_mgmt import (
    cmd_addvip, cmd_removevip, cmd_verify, cmd_unverify,
    cmd_warn, cmd_warnings, cmd_unwarn,
    cmd_resetprofile, cmd_resetstats, cmd_whois, cmd_userinfo,
)

# Moderation
from handlers.admin.moderation import (
    cmd_ban, cmd_unban, cmd_kick, cmd_mute, cmd_unmute,
    cmd_mutelist, cmd_unmuteall, cmd_forceend,
    cmd_banlist, cmd_blocked, cmd_unblock, cmd_clearblocks,
)

# Analytics
from handlers.admin.analytics import (
    cmd_stats, cmd_topusers, cmd_recent, cmd_vip, cmd_waiting, cmd_chats,
)

# Communication
from handlers.admin.communication import (
    cmd_broadcast, cmd_broadcast_media, cmd_broadcaststatus,
    cmd_pausebroadcast, cmd_resumebroadcast,
    cmd_stopbroadcast, cmd_cancelbroadcast,
    cmd_dm, cmd_notify,
    cmd_schedule, cmd_scheduled_list, cmd_scheduled_cancel,
)

# Admin management
from handlers.admin.admin_mgmt import (
    cmd_setadmin, cmd_removeadmin, cmd_adminlist,
)

# System
from handlers.admin.system import (
    cmd_health, cmd_clearchat, cmd_maintenance,
    cmd_clearcache, cmd_logs,
    cmd_forcedelete, cmd_purge, cmd_getfileid,
    cmd_appeals, cmd_resolve_appeal,
    cmd_checkchannel, cmd_gifts,
    cmd_vipinfo, cmd_vipbackfill, cmd_invites,
)

# Bad words
from handlers.admin.bad_words import (
    cmd_banword, cmd_muteword, cmd_removeword, cmd_wordlist,
)


__all__ = [
    # Helpers
    "_sidebar", "_safe_reply", "_log_action", "_deny", "_require_db", "_not_found",
    # User mgmt
    "cmd_addvip", "cmd_removevip", "cmd_verify", "cmd_unverify",
    "cmd_warn", "cmd_warnings", "cmd_unwarn",
    "cmd_resetprofile", "cmd_resetstats", "cmd_whois", "cmd_userinfo",
    # Moderation
    "cmd_ban", "cmd_unban", "cmd_kick", "cmd_mute", "cmd_unmute",
    "cmd_mutelist", "cmd_unmuteall", "cmd_forceend",
    "cmd_banlist", "cmd_blocked", "cmd_unblock", "cmd_clearblocks",
    # Analytics
    "cmd_stats", "cmd_topusers", "cmd_recent", "cmd_vip", "cmd_waiting", "cmd_chats",
    # Communication
    "cmd_broadcast", "cmd_broadcast_media", "cmd_broadcaststatus",
    "cmd_pausebroadcast", "cmd_resumebroadcast",
    "cmd_stopbroadcast", "cmd_cancelbroadcast",
    "cmd_dm", "cmd_notify",
    "cmd_schedule", "cmd_scheduled_list", "cmd_scheduled_cancel",
    # Admin mgmt
    "cmd_setadmin", "cmd_removeadmin", "cmd_adminlist",
    # System
    "cmd_health", "cmd_clearchat", "cmd_maintenance",
    "cmd_clearcache", "cmd_logs",
    "cmd_forcedelete", "cmd_purge", "cmd_getfileid",
    "cmd_appeals", "cmd_resolve_appeal",
    "cmd_checkchannel", "cmd_gifts",
    "cmd_vipinfo", "cmd_vipbackfill", "cmd_invites",
    # Bad words
    "cmd_banword", "cmd_muteword", "cmd_removeword", "cmd_wordlist",
]