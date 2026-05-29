"""Monitor and auto-monitor endpoints."""
from __future__ import annotations

import re
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_config
from app.registry import get_monitor_service
from app.repositories import (
    create_monitor, delete_monitor, get_canonical_page_id, get_monitor,
    get_post, list_monitors, list_replied_for_monitor, update_monitor,
    get_auto_monitor_config, update_auto_monitor_config,
    list_auto_monitor_schedules, add_auto_monitor_schedule,
    delete_auto_monitor_schedule, update_auto_monitor_schedule,
)

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


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
    trigger_time: str


class UpdateAutoMonitorSchedulePayload(BaseModel):
    enabled: bool


def _assert_monitor_belongs_to_active_page(monitor: dict) -> None:
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    monitor_page_id = str(monitor.get("page_id", ""))
    if monitor_page_id != page_id:
        raise HTTPException(status_code=404, detail="监控不存在")


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
        monitor_id = create_monitor(post_id=payload.post_id, interval_seconds=max(1, payload.interval_seconds))
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
    from app.repositories import delete_monitors
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    valid_ids = [mid for mid in payload.ids
                 if (m := get_monitor(mid)) and str(m.get("page_id", "")) == page_id]
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


# --- Auto-monitor ---

@router.get("/auto-monitor/settings")
async def get_auto_monitor_settings_api():
    return {"config": get_auto_monitor_config(), "schedules": list_auto_monitor_schedules()}


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
