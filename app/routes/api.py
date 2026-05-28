from __future__ import annotations

import base64
import json
import logging

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.registry import get_monitor_service
from app.config import load_config
from app.repositories import (
    create_account,
    create_monitor,
    delete_account,
    delete_comment_local,
    delete_monitor,
    delete_posts,
    clear_page_posts,
    get_account_by_id,
    get_active_account,
    get_comment,
    get_model_config,
    get_monitor,
    get_page_profile,
    get_post,
    list_monitors,
    list_accounts,
    list_posts,
    list_replied_for_monitor,
    list_comments_by_post_ids,
    set_active_account,
    update_monitor,
    update_account,
    upsert_model_config,
    get_canonical_page_id,
    get_admin_auth,
    update_admin_password,
    delete_all_admin_sessions,
    get_auto_monitor_config,
    update_auto_monitor_config,
    list_auto_monitor_schedules,
    add_auto_monitor_schedule,
    delete_auto_monitor_schedule,
    update_auto_monitor_schedule,
    get_chat_dashboard_stats,
    get_user_message_counts,
    get_chat_detailed_stats,
    save_video_analysis,
    get_video_analysis,
    update_video_analysis,
    update_video_analysis_pushed,
    parse_video_analysis_content,
)
from app.services.ai_reply import AIReplyService
from app.services.facebook import FacebookService
from app.services.sync import SyncService
from app.services.chat_sync import ChatSyncService
from app.security import PBKDF2_ITERATIONS, generate_salt, hash_password, is_strong_password, verify_password

router = APIRouter(prefix="/api")
logger = logging.getLogger("uvicorn.error")


class ReplyPayload(BaseModel):
    message: str


class CreateMonitorPayload(BaseModel):
    post_id: str
    interval_seconds: int = 300


class UpdateMonitorPayload(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = None


class BulkDeleteMonitorPayload(BaseModel):
    ids: list[int]


class UpdateAutoMonitorConfigPayload(BaseModel):
    enabled: bool | None = None
    max_posts: int | None = None


class AddAutoMonitorSchedulePayload(BaseModel):
    trigger_time: str  # HH:MM


class UpdateAutoMonitorSchedulePayload(BaseModel):
    enabled: bool


class AccountPayload(BaseModel):
    name: str = ""
    page_access_token: str
    verify_token: str
    page_id: str
    api_version: str = "v25.0"


class ModelConfigPayload(BaseModel):
    reply_api_base_url: str = ""
    reply_api_key: str = ""
    reply_model: str = ""
    video_api_base_url: str = ""
    video_api_key: str = ""
    video_model: str = ""
    prompt_template: str = "reply_prompt.j2"


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


def _assert_monitor_belongs_to_active_page(monitor: dict) -> None:
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    monitor_page_id = str(monitor.get("page_id", ""))
    if monitor_page_id != page_id:
        raise HTTPException(status_code=404, detail="监控不存在")


@router.get("/settings")
async def get_settings():
    accounts = list_accounts()
    active = get_active_account()
    model = get_model_config() or {
        "reply_api_base_url": "",
        "reply_api_key": "",
        "reply_model": "",
        "video_api_base_url": "",
        "video_api_key": "",
        "video_model": "",
    }
    return {
        "accounts": accounts,
        "active_account_id": active["id"] if active else None,
        "model": model,
    }


@router.post("/settings/accounts")
async def create_account_api(payload: AccountPayload):
    page_id = payload.page_id.strip()
    token = payload.page_access_token.strip()
    verify = payload.verify_token.strip()
    if not page_id or not token or not verify:
        raise HTTPException(status_code=400, detail="PAGE_ID、PAGE_ACCESS_TOKEN、VERIFY_TOKEN 不能为空")

    try:
        account_id = create_account(
            name=payload.name.strip() or f"账号 {page_id}",
            page_access_token=token,
            verify_token=verify,
            page_id=page_id,
            api_version=(payload.api_version.strip() or "v25.0"),
            is_active=0,
        )
        return {"status": "success", "account_id": account_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"保存账号失败: {exc}") from exc


@router.put("/settings/accounts/{account_id}")
async def update_account_api(account_id: int, payload: AccountPayload):
    account = get_account_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    page_id = payload.page_id.strip()
    token = payload.page_access_token.strip()
    verify = payload.verify_token.strip()
    if not page_id or not token or not verify:
        raise HTTPException(status_code=400, detail="PAGE_ID、PAGE_ACCESS_TOKEN、VERIFY_TOKEN 不能为空")

    try:
        update_account(
            account_id,
            name=payload.name.strip() or f"账号 {page_id}",
            page_access_token=token,
            verify_token=verify,
            page_id=page_id,
            api_version=(payload.api_version.strip() or "v25.0"),
        )
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"更新账号失败: {exc}") from exc


@router.post("/settings/accounts/{account_id}/activate")
async def activate_account_api(account_id: int):
    account = get_account_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    set_active_account(account_id)
    return {"status": "success"}


@router.delete("/settings/accounts/{account_id}")
async def delete_account_api(account_id: int):
    account = get_account_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    delete_account(account_id)
    return {"status": "success"}


@router.get("/settings/accounts/export")
async def export_accounts_api():
    accounts = list_accounts()
    # Remove sensitive/internal fields if needed, but for "batch import/export" we keep tokens
    export_data = []
    for acc in accounts:
        export_data.append({
            "name": acc["name"],
            "page_id": acc["page_id"],
            "page_access_token": acc["page_access_token"],
            "verify_token": acc["verify_token"],
            "api_version": acc["api_version"],
        })
    return export_data


@router.post("/settings/accounts/import")
async def import_accounts_api(payload: list[dict]):
    try:
        from app.repositories import bulk_import_accounts
        count = bulk_import_accounts(payload)
        return {"status": "success", "count": count}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"导入失败: {exc}")


@router.put("/settings/model")
async def update_model_api(payload: ModelConfigPayload):
    upsert_model_config(
        reply_api_base_url=payload.reply_api_base_url.strip(),
        reply_api_key=payload.reply_api_key.strip(),
        reply_model=payload.reply_model.strip(),
        video_api_base_url=payload.video_api_base_url.strip(),
        video_api_key=payload.video_api_key.strip(),
        video_model=payload.video_model.strip(),
        prompt_template=payload.prompt_template.strip() or "reply_prompt.j2",
    )
    return {"status": "success"}


@router.post("/settings/model/test")
async def test_model_api(payload: ModelConfigPayload):
    from app.config import AppConfig
    temp_config = AppConfig(
        account_id=0,
        account_name="",
        page_access_token="",
        verify_token="",
        page_id="",
        reply_api_base_url=payload.reply_api_base_url.strip(),
        reply_api_key=payload.reply_api_key.strip(),
        reply_model=payload.reply_model.strip(),
        video_api_base_url=payload.video_api_base_url.strip(),
        video_api_key=payload.video_api_key.strip(),
        video_model=payload.video_model.strip(),
        prompt_template=payload.prompt_template.strip() or "reply_prompt.j2",
    )
    ai_service = AIReplyService(temp_config)
    try:
        result = await ai_service.test_reply_connection()
        return {"status": "success", "message": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/model/test-video")
async def test_video_model_api(payload: ModelConfigPayload):
    from app.config import AppConfig
    temp_config = AppConfig(
        account_id=0,
        account_name="",
        page_access_token="",
        verify_token="",
        page_id="",
        reply_api_base_url=payload.reply_api_base_url.strip(),
        reply_api_key=payload.reply_api_key.strip(),
        reply_model=payload.reply_model.strip(),
        video_api_base_url=payload.video_api_base_url.strip(),
        video_api_key=payload.video_api_key.strip(),
        video_model=payload.video_model.strip(),
        prompt_template=payload.prompt_template.strip() or "reply_prompt.j2",
    )
    ai_service = AIReplyService(temp_config)
    try:
        result = await ai_service.test_video_connection()
        return {"status": "success", "message": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/change-password")
async def change_admin_password(payload: ChangePasswordPayload):
    auth = get_admin_auth()
    if auth is None:
        raise HTTPException(status_code=500, detail="管理员账号不存在")

    old_ok = verify_password(
        payload.old_password,
        salt_hex=str(auth.get("password_salt", "")),
        expected_hash_hex=str(auth.get("password_hash", "")),
        iterations=int(auth.get("password_iterations", PBKDF2_ITERATIONS)),
    )
    if not old_ok:
        raise HTTPException(status_code=400, detail="旧密码错误")

    if not is_strong_password(payload.new_password):
        raise HTTPException(status_code=400, detail="新密码不符合强密码要求（至少16位，包含大小写字母、数字和符号）")

    salt = generate_salt()
    pwd_hash = hash_password(payload.new_password, salt, PBKDF2_ITERATIONS)
    update_admin_password(
        password_hash=pwd_hash,
        password_salt=salt,
        password_iterations=PBKDF2_ITERATIONS,
    )

    delete_all_admin_sessions()
    return {"status": "success", "message": "密码已更新，请重新登录"}


# ---------------------------------------------------------------------------
# Page profile
# ---------------------------------------------------------------------------

@router.get("/page-profile")
async def page_profile():
    config = load_config()
    profile = get_page_profile(page_id=config.page_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="尚未同步到主页信息")
    return profile


@router.post("/page-profile/refresh")
async def refresh_page_profile():
    config = load_config()
    async with FacebookService(config) as facebook:
        try:
            profile = await facebook.fetch_page_profile()
            from app.repositories import upsert_page_profile
            upsert_page_profile(profile)
            return profile
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"刷新主页信息失败: {exc}") from exc


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@router.post("/sync")
async def sync_data(limit: int = 0, since: str = "", until: str = "", all_posts: bool = True):
    config = load_config()
    service = SyncService(config)
    try:
        return {
            "status": "success",
            "summary": await service.sync_all(post_limit=limit, since=since, until=until, all_posts=all_posts),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sync/stream")
async def sync_data_stream(limit: int = 0, since: str = "", until: str = "", all_posts: bool = True, sync_comments: bool = True):
    config = load_config()
    service = SyncService(config)

    async def event_generator():
        try:
            async for step in service.sync_all_gen(post_limit=limit, since=since, until=until, all_posts=all_posts, sync_comments=sync_comments):
                # Format as SSE event
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/sync/stop")
async def stop_sync():
    from app.task import cancel_task
    cancel_task("post_sync")
    return {"status": "stopped"}


@router.post("/sync/posts/{post_id}")
async def sync_single_post_api(post_id: str):
    config = load_config()
    service = SyncService(config)
    try:
        return {"status": "success", "summary": await service.sync_post(post_id)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@router.get("/posts/{post_id}/comments")
async def get_post_comments(post_id: str):
    comments_dict = list_comments_by_post_ids([post_id])
    return comments_dict.get(post_id, [])


@router.get("/attachments/{comment_id}")
async def get_attachment_image(comment_id: str):
    """Serve attachment image for a comment as binary response."""
    from app.repositories import get_comment_attachments
    from fastapi.responses import Response

    attachments = get_comment_attachments(comment_id)
    if not attachments:
        raise HTTPException(status_code=404, detail="未找到附件")

    att = attachments[0]
    data = att.get("data")
    if not data:
        raise HTTPException(status_code=404, detail="附件数据为空")

    media_type = att.get("media_type", "")
    # All compressible types are stored as WebP
    content_type = "image/webp" if media_type in ("sticker", "photo", "animated_image_share") else "application/octet-stream"

    return Response(content=data, media_type=content_type)


@router.post("/comments/{comment_id}/reply")
async def create_reply(comment_id: str, payload: ReplyPayload):
    config = load_config()
    try:
        async with FacebookService(config) as facebook:
            comment = get_comment(comment_id)
            await facebook.send_reply(comment_id, payload.message)
        sync_service = SyncService(config)
        if comment is not None:
            summary = await sync_service.sync_post(str(comment.get("post_id", "")))
        else:
            summary = await sync_service.sync_all(post_limit=1, all_posts=False)
        return {"status": "success", "summary": summary}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/comments/{comment_id}/ai-reply")
async def create_ai_reply(comment_id: str):
    config = load_config()
    comment = get_comment(comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="评论不存在")

    post = get_post(comment["post_id"])
    if post is None:
        raise HTTPException(status_code=404, detail="评论所属帖子不存在")

    profile = get_page_profile(page_id=config.page_id) or {}
    ai_service = AIReplyService(config)
    try:
        message = await ai_service.generate_reply(
            page_name=profile.get("name", ""),
            post_message=post.get("message", ""),
            comment_message=comment.get("message", ""),
            comment_author=comment.get("author_name", "匿名用户"),
        )
        return {"status": "success", "message": message}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/comments/{comment_id}")
async def remove_comment(comment_id: str):
    config = load_config()
    async with FacebookService(config) as facebook:
        try:
            deleted = await facebook.delete_comment(comment_id)
            if not deleted:
                raise HTTPException(status_code=500, detail="Facebook 未确认删除成功")
            delete_comment_local(comment_id)
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Posts (for monitor creation form)
# ---------------------------------------------------------------------------

@router.get("/prompts")
async def list_prompts_api():
    from app.config import PROJECT_ROOT
    import os
    prompts_dir = PROJECT_ROOT / "prompts"
    if not prompts_dir.exists():
        return {"data": []}
    
    config = load_config()
    prompts = []
    
    for filename in os.listdir(prompts_dir):
        if filename.endswith(".j2"):
            file_path = prompts_dir / filename
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                prompts.append({
                    "filename": filename,
                    "content": content,
                    "is_active": filename == config.prompt_template
                })
            except Exception:
                pass
            
    return {"data": prompts}

class ActivatePromptPayload(BaseModel):
    filename: str

@router.post("/prompts/activate")
async def activate_prompt_api(payload: ActivatePromptPayload):
    from app.repositories import get_model_config, upsert_model_config
    model = get_model_config() or {}
    
    upsert_model_config(
        reply_api_base_url=str(model.get("reply_api_base_url", "")),
        reply_api_key=str(model.get("reply_api_key", "")),
        reply_model=str(model.get("reply_model", "")),
        video_api_base_url=str(model.get("video_api_base_url", "")),
        video_api_key=str(model.get("video_api_key", "")),
        video_model=str(model.get("video_model", "")),
        prompt_template=payload.filename.strip()
    )
    return {"status": "success"}

@router.get("/posts")
async def list_posts_api(limit: int = 100):
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    posts = list_posts(page_id=page_id, limit=limit)
    monitors = {m["post_id"]: m for m in list_monitors(page_id=page_id)}
    result = []
    for post in posts:
        item = {
            "id": post["id"],
            "message": post.get("message", ""),
            "created_time": post.get("created_time", ""),
            "permalink_url": post.get("permalink_url", ""),
            "has_monitor": post["id"] in monitors,
        }
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------

@router.get("/monitors")
async def list_monitors_api():
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    return list_monitors(page_id=page_id)


@router.post("/monitors")
async def create_monitor_api(payload: CreateMonitorPayload):
    post = get_post(payload.post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="帖子不存在，请先同步数据")
    try:
        monitor_id = create_monitor(
            post_id=payload.post_id,
            interval_seconds=max(1, payload.interval_seconds),
        )
        return {"status": "success", "monitor_id": monitor_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/monitors/{monitor_id}")
async def get_monitor_api(monitor_id: int):
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="监控不存在")
    _assert_monitor_belongs_to_active_page(monitor)
    return monitor


@router.patch("/monitors/{monitor_id}")
async def update_monitor_api(monitor_id: int, payload: UpdateMonitorPayload):
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="监控不存在")
    _assert_monitor_belongs_to_active_page(monitor)
    kwargs: dict = {}
    if payload.enabled is not None:
        kwargs["enabled"] = int(payload.enabled)
    if payload.interval_seconds is not None:
        kwargs["interval_seconds"] = max(1, payload.interval_seconds)
    update_monitor(monitor_id, **kwargs)
    return {"status": "success"}


@router.delete("/monitors/{monitor_id}")
async def delete_monitor_api(monitor_id: int):
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="监控不存在")
    _assert_monitor_belongs_to_active_page(monitor)
    delete_monitor(monitor_id)
    return {"status": "success"}


@router.post("/monitors/bulk-delete")
async def delete_monitors_api(payload: BulkDeleteMonitorPayload):
    # For bulk operations, we check each monitor's page_id if we want strict security,
    # or just trust the admin has already authorized the active page.
    # To be safe, we'll verify all IDs belong to the active page.
    from app.repositories import delete_monitors
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    
    valid_ids = []
    for mid in payload.ids:
        monitor = get_monitor(mid)
        if monitor and str(monitor.get("page_id", "")) == page_id:
            valid_ids.append(mid)
    
    if valid_ids:
        delete_monitors(valid_ids)
    
    return {"status": "success", "deleted_count": len(valid_ids)}


@router.post("/monitors/{monitor_id}/run")
async def run_monitor_now_api(monitor_id: int):
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="监控不存在")
    _assert_monitor_belongs_to_active_page(monitor)
    try:
        svc = get_monitor_service()
        result = await svc.run_monitor_now(monitor_id)
        return {"status": "success", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/monitors/{monitor_id}/replied")
async def list_replied_api(monitor_id: int, limit: int = 50):
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="监控不存在")
    _assert_monitor_belongs_to_active_page(monitor)
    return list_replied_for_monitor(monitor_id, limit=limit)


# --- Chat (Private Message) Endpoints ---

@router.get("/chats/stats")
async def get_chat_stats_api():
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    stats = get_chat_dashboard_stats(page_id)
    detailed_stats = get_chat_detailed_stats(page_id)
    return {
        "stats": stats,
        "detailed_stats": detailed_stats
    }


@router.get("/chats/user-ranking")
async def get_chat_user_ranking_api(limit: int = 100):
    from app.repositories import get_user_ranking_stats
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    return get_user_ranking_stats(page_id, limit=limit)


from app.task import get_task as _get_task, cancel_task as _cancel_task, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED

@router.get("/sync/status")
async def get_sync_status_api(task: str):
    """Query current progress of a specific sync task. Legacy endpoint."""
    t = _get_task(task)
    if not t:
        return {"msg": "No active task", "done": True}
    # Return in legacy format for backward compatibility
    return {
        "msg": t.get("message", ""),
        "percent": t.get("progress", 0),
        "done": t["status"] in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED),
        "error": t["status"] == STATUS_FAILED,
        **(t.get("result", {}) if isinstance(t.get("result"), dict) else {}),
    }


@router.get("/tasks/{task_id}")
async def get_task_api(task_id: str):
    """Get task status by ID."""
    t = _get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@router.post("/tasks/{task_id}/cancel")
async def cancel_task_api(task_id: str):
    """Cancel a running task."""
    cancelled = _cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail="任务不存在或未在运行")
    return {"status": "cancelled"}


@router.get("/chats/sync")
async def sync_chats_api(full: bool = False):
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    fb_service = FacebookService(config)
    sync_service = ChatSyncService(fb_service)

    async def event_generator():
        async for event in sync_service.sync_all_chats(page_id, full_sync=full):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


# ---------------------------------------------------------------------------
# Auto-monitor
# ---------------------------------------------------------------------------

@router.get("/auto-monitor/settings")
async def get_auto_monitor_settings_api():
    return {
        "config": get_auto_monitor_config(),
        "schedules": list_auto_monitor_schedules(),
    }


@router.patch("/auto-monitor/config")
async def update_auto_monitor_config_api(payload: UpdateAutoMonitorConfigPayload):
    kwargs = {}
    if payload.enabled is not None:
        kwargs["enabled"] = 1 if payload.enabled else 0
    if payload.max_posts is not None:
        kwargs["max_posts"] = max(1, payload.max_posts)
    
    update_auto_monitor_config(**kwargs)
    return {"status": "success"}


@router.post("/auto-monitor/schedules")
async def add_auto_monitor_schedule_api(payload: AddAutoMonitorSchedulePayload):
    # Validate HH:MM
    import re
    if not re.match(r"^\d{2}:\d{2}$", payload.trigger_time):
        raise HTTPException(status_code=400, detail="时间格式必须为 HH:MM")
    
    try:
        h, m = map(int, payload.trigger_time.split(":"))
        if h < 0 or h > 23 or m < 0 or m > 59:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="时间数值不合法")

    add_auto_monitor_schedule(payload.trigger_time)
    return {"status": "success"}


@router.patch("/auto-monitor/schedules/{schedule_id}")
async def update_auto_monitor_schedule_api(schedule_id: int, payload: UpdateAutoMonitorSchedulePayload):
    update_auto_monitor_schedule(schedule_id, enabled=(1 if payload.enabled else 0))
    return {"status": "success"}


@router.delete("/auto-monitor/schedules/{schedule_id}")
async def delete_auto_monitor_schedule_api(schedule_id: int):
    delete_auto_monitor_schedule(schedule_id)
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Bulk delete posts
# ---------------------------------------------------------------------------

class DeletePostsPayload(BaseModel):
    post_ids: list[str]

@router.post("/posts/delete")
async def delete_posts_api(payload: DeletePostsPayload):
    try:
        delete_posts(payload.post_ids)
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/posts/clear-all")
async def clear_posts_api():
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    try:
        clear_page_posts(page_id)
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Video Analysis
# ---------------------------------------------------------------------------

@router.post("/posts/{post_id}/analyze")
async def analyze_post_video(post_id: str, force: bool = False):
    """Analyze a video post: download from Facebook → base64 → LLM → return result.
    Returns cached result unless force=true.
    """
    from app.task import create_task, update_task, is_task_running, STATUS_SUCCESS, STATUS_FAILED

    task_key = f"video_analysis_{post_id}"

    # Check cache first
    if not force:
        cached = get_video_analysis(post_id)
        if cached:
            return {"status": "success", "result": cached["content"], "cached": True}

    try:
        return await _do_analyze(post_id, force, task_key)
    except Exception:
        raise
    finally:
        task_mod = __import__("app.task", fromlist=["update_task", "STATUS_SUCCESS"])
        task_mod.update_task(task_key, status=task_mod.STATUS_SUCCESS)


async def _do_analyze(post_id: str, force: bool, task_key: str):
    from app.task import update_task

    update_task(task_key, message="正在获取视频信息...", progress=10)

    post = get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="帖子不存在")

    if post.get("type") != "video":
        raise HTTPException(status_code=400, detail="该帖子不是视频类型")

    raw_json = post.get("raw_json")
    if not raw_json:
        raise HTTPException(status_code=400, detail="帖子缺少原始数据")

    raw = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    video_id = raw.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="帖子数据中未找到 video_id")

    config = load_config()
    if not config.page_access_token:
        raise HTTPException(status_code=500, detail="未配置 page_access_token")

    model_config = get_model_config()
    if not model_config or not (model_config.get("video_api_key") or model_config.get("reply_api_key")):
        raise HTTPException(status_code=500, detail="未配置 AI 模型")

    # Step 1: Get video source URL from Facebook
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(
                f"{config.graph_base_url}/{video_id}",
                params={
                    "fields": "source,permalink_url,description,length",
                    "access_token": config.page_access_token,
                },
            )
            resp.raise_for_status()
            video_info = resp.json()
    except Exception as exc:
        logger.error("[analyze] Failed to get video source for post=%s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"获取视频信息失败: {exc}")

    video_source = video_info.get("source")
    if not video_source:
        raise HTTPException(status_code=502, detail="Facebook 未返回视频下载链接，可能需要额外权限")

    # Step 2: Download video
    update_task(task_key, message="正在下载视频...", progress=30)
    video_bytes = None
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(video_source)
            resp.raise_for_status()
            video_bytes = resp.content
    except Exception as exc:
        logger.error("[analyze] Failed to download video for post=%s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"下载视频失败: {exc}")

    size_mb = len(video_bytes) / (1024 * 1024)
    if size_mb > 50:
        raise HTTPException(status_code=413, detail=f"视频文件过大 ({size_mb:.1f} MB)，超过 50MB 限制")

    # Step 3: Base64 encode and send to LLM
    update_task(task_key, message="正在分析视频内容（可能需要 1-2 分钟）...", progress=60)
    b64 = base64.b64encode(video_bytes).decode()
    del video_bytes  # Free memory immediately

    ai_service = AIReplyService(config)
    try:
        result = await ai_service.analyze_video(b64)
    except Exception as exc:
        logger.error("[analyze] LLM analysis failed for post=%s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"AI 分析失败: {exc}")

    # Save to database
    title = (post.get("message") or "")[:200]
    post_time = _parse_fb_timestamp(post.get("created_time", ""))
    try:
        save_video_analysis(post_id, title, result, post_time)
    except Exception as exc:
        logger.warning("[analyze] Failed to save analysis result: %s", exc)

    return {"status": "success", "result": result, "cached": False}


@router.put("/posts/{post_id}/analyze")
async def update_post_video_analysis(post_id: str, payload: dict[str, str]):
    """Update (manually correct) the location field of a video analysis."""
    new_location = (payload.get("location") or "").strip()
    if not new_location:
        raise HTTPException(status_code=400, detail="地点不能为空")

    existing = get_video_analysis(post_id)
    if not existing:
        raise HTTPException(status_code=404, detail="未找到该帖子的分析记录")

    content = existing.get("content", "")
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="该分析结果为旧格式，无法修正地点字段。请重新分析。")

    data["location"] = new_location
    if not update_video_analysis(post_id, json.dumps(data, ensure_ascii=False)):
        raise HTTPException(status_code=404, detail="未找到该帖子的分析记录")
    return {"status": "success"}


def _parse_fb_timestamp(ts: str) -> int:
    """Parse Facebook ISO timestamp to Unix timestamp (int)."""
    if not ts:
        return 0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


# ---- Schedule management endpoints ----



@router.post("/video/batch-analyze")
async def batch_analyze_videos():
    """Analyze all video-type posts that don't have analysis yet."""
    from app.task import create_task, update_task, get_task, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED

    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    posts = list_posts(page_id=page_id, limit=200)

    video_posts = [p for p in posts if p.get("type") == "video"]
    unanalyzed = []
    for p in video_posts:
        existing = get_video_analysis(p["id"])
        if not existing:
            unanalyzed.append(p)

    if not unanalyzed:
        return {"status": "success", "total": 0, "msg": "没有需要分析的视频"}

    task_key = "batch_video_analysis"
    total = len(unanalyzed)
    create_task(task_key, "批量视频分析")
    update_task(task_key, status="running", message=f"开始批量分析 {total} 个视频...", progress=0,
                result={"total": total, "completed": 0})

    results = {"success": 0, "failed": 0, "errors": []}
    for i, post in enumerate(unanalyzed):
        # Check for cooperative cancellation
        task = get_task(task_key)
        if task and task["status"] == STATUS_CANCELED:
            return {"status": "cancelled", **results}

        pct = int((i / total) * 100)
        update_task(task_key, message=f"正在分析第 {i+1}/{total} 个视频...", progress=pct,
                    result={"total": total, "completed": i})

        try:
            await _do_analyze(post["id"], True, f"video_analysis_{post['id']}")
            results["success"] += 1
        except Exception as exc:
            results["failed"] += 1
            results["errors"].append({"post_id": post["id"], "error": str(exc)})
            logger.error("[batch-analyze] Failed for post=%s: %s", post["id"], exc)

    update_task(task_key, status=STATUS_SUCCESS,
                message=f"批量分析完成：成功 {results['success']}，失败 {results['failed']}", progress=100,
                result={"total": total, "completed": total})
    return {"status": "success", **results}


@router.post("/video/push/{post_id}")
async def push_video_analysis(post_id: str):
    """Push a video analysis result to the external schedule API."""
    import os
    from datetime import datetime, timezone

    push_url = os.getenv("PUSH_URL", "").strip()
    push_token = os.getenv("PUSH_TOKEN", "").strip()
    if not push_url or not push_token:
        raise HTTPException(status_code=500, detail="未配置 PUSH_URL / PUSH_TOKEN 环境变量")

    analysis = get_video_analysis(post_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="未找到该帖子的分析记录")

    content_raw = analysis.get("content", "")
    parsed = parse_video_analysis_content(content_raw)
    if parsed:
        content = f"{parsed['location']}\n{parsed['behavior']}\n{parsed['environment']}"
    else:
        content = content_raw

    post = get_post(post_id)
    title = (post.get("message") or "")[:150] if post else ""
    post_time = _parse_fb_timestamp(post.get("created_time", "")) if post else 0

    payload = {
        "postsId": post_id,
        "postTime": post_time,
        "title": title,
        "content": content,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                push_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Cookie": f"mgr_token={push_token}",
                },
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.error("[push] Failed to push post=%s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"推送失败: {exc}")

    now = datetime.now(timezone.utc).isoformat()
    update_video_analysis_pushed(post_id, now)

    return {"status": "success", "pushed_at": now}
