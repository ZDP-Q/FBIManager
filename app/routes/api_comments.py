"""Comment and post endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import load_config
from app.repositories import (
    delete_comment_local, get_comment, get_comment_attachments, get_page_profile,
    get_post, list_comments_by_post_ids, list_posts, list_monitors,
    delete_posts, clear_page_posts, get_canonical_page_id,
)
from app.services.ai_reply import AIReplyService
from app.services.facebook import FacebookService
from app.services.sync import SyncService

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


class ReplyPayload(BaseModel):
    message: str


class DeletePostsPayload(BaseModel):
    post_ids: list[str]


class ActivatePromptPayload(BaseModel):
    filename: str


@router.get("/posts/{post_id}/comments")
async def get_post_comments(post_id: str):
    comments_dict = list_comments_by_post_ids([post_id])
    return comments_dict.get(post_id, [])


@router.get("/attachments/{comment_id}")
async def get_attachment_image(comment_id: str):
    attachments = get_comment_attachments(comment_id)
    if not attachments:
        raise HTTPException(status_code=404, detail="未找到附件")
    att = attachments[0]
    data = att.get("data")
    if not data:
        raise HTTPException(status_code=404, detail="附件数据为空")
    media_type = att.get("media_type", "")
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
                prompts.append({"filename": filename, "content": content, "is_active": filename == config.prompt_template})
            except Exception:
                pass
    return {"data": prompts}


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
        prompt_template=payload.filename.strip(),
    )
    return {"status": "success"}


@router.get("/posts")
async def list_posts_api(limit: int = 100):
    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    posts = list_posts(page_id=page_id, limit=limit)
    monitors = {m["post_id"]: m for m in list_monitors(page_id=page_id)}
    return [{
        "id": post["id"], "message": post.get("message", ""),
        "created_time": post.get("created_time", ""), "permalink_url": post.get("permalink_url", ""),
        "has_monitor": post["id"] in monitors,
    } for post in posts]


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
