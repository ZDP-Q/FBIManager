"""API router — includes all sub-routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.routes.api_settings import router as settings_router
from app.routes.api_sync import router as sync_router
from app.routes.api_comments import router as comments_router
from app.routes.api_monitors import router as monitors_router
from app.routes.api_chats import router as chats_router
from app.routes.api_video import router as video_router

router = APIRouter(prefix="/api")

router.include_router(settings_router)
router.include_router(sync_router)
router.include_router(comments_router)
router.include_router(monitors_router)
router.include_router(chats_router)
router.include_router(video_router)
