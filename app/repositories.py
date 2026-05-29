"""Repositories facade — re-exports from domain-specific modules.

All functions are available through this module for backward compatibility.
New code should import from app.repos.<domain> directly.
"""
from __future__ import annotations

# Account & model config
from app.repos.account import (
    list_accounts,
    get_active_account,
    get_account_by_id,
    get_account_by_page_id,
    get_account_by_verify_token,
    create_account,
    update_account,
    delete_account,
    set_active_account,
    bulk_import_accounts,
    get_model_config,
    upsert_model_config,
)

# Posts & page profiles
from app.repos.post import (
    upsert_page_profile,
    get_page_profile,
    get_canonical_page_id,
    upsert_post,
    list_posts,
    delete_posts,
    clear_page_posts,
    get_post,
)

# Comments
from app.repos.comment import (
    replace_comments_for_post,
    get_comment,
    list_comments_by_post_ids,
    delete_comment_local,
    get_screened_comment_ids,
    mark_comments_screened,
    get_latest_comment_time,
    count_pending_comments,
    get_oldest_pending_comment_time,
    list_pending_comments,
    insert_comment_attachment,
    get_comment_attachments,
    has_attachment,
    has_replied,
    mark_replied,
    unmark_replied,
    list_replied_for_monitor,
    list_replied_for_post,
    upsert_comment,
)

# Monitors
from app.repos.monitor import (
    create_monitor,
    list_monitors,
    get_monitor,
    get_monitor_by_post,
    update_monitor,
    list_monitored_post_ids,
    delete_monitor,
    delete_monitors,
    get_auto_monitor_config,
    update_auto_monitor_config,
    list_auto_monitor_schedules,
    add_auto_monitor_schedule,
    delete_auto_monitor_schedule,
    update_auto_monitor_schedule,
    mark_auto_monitor_triggered,
)

# Auth
from app.repos.auth import (
    get_admin_auth,
    update_admin_password,
    create_admin_session,
    get_admin_session,
    touch_admin_session,
    delete_admin_session,
    delete_all_admin_sessions,
    cleanup_expired_admin_sessions,
    is_ip_locked,
    register_failed_login,
    clear_login_attempts,
)

# Chat
from app.repos.chat import (
    upsert_page_conversation,
    upsert_conversation_message,
    bulk_upsert_conversation_messages,
    get_latest_conversation_update,
    get_latest_message_time,
    get_conversation_updated_time,
    check_message_exists,
    get_chat_dashboard_stats,
    get_user_message_counts,
    get_chat_detailed_stats,
    get_user_ranking_stats,
)

# Video analysis
from app.repos.video import (
    save_video_analysis,
    update_video_analysis,
    get_video_analysis,
    parse_video_analysis_content,
    list_video_analyses,
    update_video_analysis_pushed,
    list_posts_with_analysis,
)

__all__ = [
    # Account
    "list_accounts", "get_active_account", "get_account_by_id", "get_account_by_page_id",
    "get_account_by_verify_token", "create_account", "update_account", "delete_account",
    "set_active_account", "bulk_import_accounts", "get_model_config", "upsert_model_config",
    # Post
    "upsert_page_profile", "get_page_profile", "get_canonical_page_id", "upsert_post",
    "list_posts", "delete_posts", "clear_page_posts", "get_post",
    # Comment
    "replace_comments_for_post", "get_comment", "list_comments_by_post_ids",
    "delete_comment_local", "get_screened_comment_ids", "mark_comments_screened",
    "get_latest_comment_time", "count_pending_comments", "get_oldest_pending_comment_time",
    "list_pending_comments", "insert_comment_attachment", "get_comment_attachments",
    "has_attachment", "has_replied", "mark_replied", "unmark_replied",
    "list_replied_for_monitor", "list_replied_for_post", "upsert_comment",
    # Monitor
    "create_monitor", "list_monitors", "get_monitor", "get_monitor_by_post",
    "update_monitor", "list_monitored_post_ids", "delete_monitor", "delete_monitors",
    "get_auto_monitor_config", "update_auto_monitor_config", "list_auto_monitor_schedules",
    "add_auto_monitor_schedule", "delete_auto_monitor_schedule", "update_auto_monitor_schedule",
    "mark_auto_monitor_triggered",
    # Auth
    "get_admin_auth", "update_admin_password", "create_admin_session", "get_admin_session",
    "touch_admin_session", "delete_admin_session", "delete_all_admin_sessions",
    "cleanup_expired_admin_sessions", "is_ip_locked", "register_failed_login", "clear_login_attempts",
    # Chat
    "upsert_page_conversation", "upsert_conversation_message", "bulk_upsert_conversation_messages",
    "get_latest_conversation_update", "get_latest_message_time", "get_conversation_updated_time",
    "check_message_exists", "get_chat_dashboard_stats", "get_user_message_counts",
    "get_chat_detailed_stats", "get_user_ranking_stats",
    # Video
    "save_video_analysis", "update_video_analysis", "get_video_analysis",
    "parse_video_analysis_content", "list_video_analyses", "update_video_analysis_pushed",
    "list_posts_with_analysis",
]
