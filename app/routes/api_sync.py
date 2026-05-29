"""Sync and task management endpoints."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import load_config
from app.services.sync import SyncService

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.post("/sync")
async def sync_data(limit: int = 0, since: str = "", until: str = "", all_posts: bool = True):
    config = load_config()
    service = SyncService(config)
    try:
        return {"status": "success", "summary": await service.sync_all(post_limit=limit, since=since, until=until, all_posts=all_posts)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sync/stream")
async def sync_data_stream(limit: int = 0, since: str = "", until: str = "", all_posts: bool = True, sync_comments: bool = True):
    config = load_config()
    service = SyncService(config)

    async def event_generator():
        try:
            async for step in service.sync_all_gen(post_limit=limit, since=since, until=until, all_posts=all_posts, sync_comments=sync_comments):
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


from app.task import get_task as _get_task, cancel_task as _cancel_task, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED


@router.get("/sync/status")
async def get_sync_status_api(task: str):
    """Query current progress of a specific sync task. Legacy endpoint."""
    t = _get_task(task)
    if not t:
        return {"msg": "No active task", "done": True}
    return {
        "msg": t.get("message", ""),
        "percent": t.get("progress", 0),
        "done": t["status"] in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED),
        "error": t["status"] == STATUS_FAILED,
        **(t.get("result", {}) if isinstance(t.get("result"), dict) else {}),
    }


@router.get("/tasks/{task_id}")
async def get_task_api(task_id: str):
    t = _get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@router.post("/tasks/{task_id}/cancel")
async def cancel_task_api(task_id: str):
    cancelled = _cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail="任务不存在或未在运行")
    return {"status": "cancelled"}
