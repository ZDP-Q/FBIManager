"""Chat (private message) endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import load_config
from app.repositories import (
    get_canonical_page_id, get_chat_dashboard_stats,
    get_chat_detailed_stats, get_user_ranking_stats,
)
from app.services.facebook import FacebookService
from app.services.chat_sync import ChatSyncService

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.get("/chats/stats")
async def get_chat_stats_api():
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    stats = get_chat_dashboard_stats(page_id)
    return JSONResponse(
        content={
            "page_id": page_id,
            "stats": stats,
            "detailed_stats": get_chat_detailed_stats(page_id),
        },
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/chats/user-ranking")
async def get_chat_user_ranking_api(limit: int = 100):
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    return get_user_ranking_stats(page_id, limit=limit)


@router.post("/chats/sync")
async def sync_chats_api(full: bool = False):
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    fb_service = FacebookService(config)
    sync_service = ChatSyncService(fb_service)
    result = await sync_service.start_sync(page_id, full_sync=full)
    return result
