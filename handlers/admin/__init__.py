"""Admin handlers package — re-exports all commands."""

from handlers.admin._helpers import (
    _sidebar, _safe_reply, _log_action, _deny, _require_db, _not_found,
)

from handlers.admin.user_mgmt import (
    cmd_addvip, cmd_removevip, cmd_verify, cmd_unverify,
    cmd_warn, cmd_warnings, cmd_unwarn,
    cmd_resetprofile, cmd_resetstats, cmd_whois, cmd_userinfo,
)

from handlers.admin.moderation import (
    cmd_ban, cmd_unban, cmd_kick, cmd_mute, cmd_unmute,
    cmd_mutelist, cmd_unmuteall, cmd_forceend,
    cmd_banlist, cmd_blocked, cmd_unblock, cmd_clearblocks,
    cmd_endallchats,                                       # ⭐ NEW
)

from handlers.admin.analytics import (
    cmd_stats, cmd_topusers, cmd_recent, cmd_vip, cmd_waiting, cmd_chats,
)

from handlers.admin.communication import (
    cmd_broadcast, cmd_broadcast_media, cmd_broadcaststatus,
    cmd_pausebroadcast, cmd_resumebroadcast,
    cmd_stopbroadcast, cmd_cancelbroadcast,
    cmd_dm, cmd_notify,
    cmd_schedule, cmd_scheduled_list, cmd_scheduled_cancel,
)

from handlers.admin.admin_mgmt import (
    cmd_setadmin, cmd_removeadmin, cmd_adminlist,
)

from handlers.admin.system import (
    cmd_health, cmd_clearchat, cmd_maintenance,
    cmd_clearcache, cmd_logs,
    cmd_forcedelete, cmd_purge, cmd_getfileid,
    cmd_appeals, cmd_resolve_appeal,
    cmd_checkchannel, cmd_gifts,
    cmd_vipinfo, cmd_vipbackfill, cmd_invites,
    cmd_chatdb,
    cmd_debug_owner,
    cmd_reports,
    cmd_logtest,
)

from handlers.admin.bad_words import (
    cmd_banword, cmd_muteword, cmd_removeword, cmd_wordlist,
)


__all__ = [
    "_sidebar", "_safe_reply", "_log_action", "_deny", "_require_db", "_not_found",
    "cmd_addvip", "cmd_removevip", "cmd_verify", "cmd_unverify",
    "cmd_warn", "cmd_warnings", "cmd_unwarn",
    "cmd_resetprofile", "cmd_resetstats", "cmd_whois", "cmd_userinfo",
    "cmd_ban", "cmd_unban", "cmd_kick", "cmd_mute", "cmd_unmute",
    "cmd_mutelist", "cmd_unmuteall", "cmd_forceend",
    "cmd_banlist", "cmd_blocked", "cmd_unblock", "cmd_clearblocks",
    "cmd_endallchats",                                     # ⭐ NEW
    "cmd_stats", "cmd_topusers", "cmd_recent", "cmd_vip", "cmd_waiting", "cmd_chats",
    "cmd_broadcast", "cmd_broadcast_media", "cmd_broadcaststatus",
    "cmd_pausebroadcast", "cmd_resumebroadcast",
    "cmd_stopbroadcast", "cmd_cancelbroadcast",
    "cmd_dm", "cmd_notify",
    "cmd_schedule", "cmd_scheduled_list", "cmd_scheduled_cancel",
    "cmd_setadmin", "cmd_removeadmin", "cmd_adminlist",
    "cmd_health", "cmd_clearchat", "cmd_maintenance",
    "cmd_clearcache", "cmd_logs",
    "cmd_forcedelete", "cmd_purge", "cmd_getfileid",
    "cmd_appeals", "cmd_resolve_appeal",
    "cmd_checkchannel", "cmd_gifts",
    "cmd_vipinfo", "cmd_vipbackfill", "cmd_invites",
    "cmd_chatdb",
    "cmd_debug_owner",
    "cmd_reports",
    "cmd_logtest",
    "cmd_banword", "cmd_muteword", "cmd_removeword", "cmd_wordlist",
]