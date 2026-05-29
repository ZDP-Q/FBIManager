"""Video analysis, batch analyze, and push endpoints."""
from __future__ import annotations

import base64
import json
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException

from app.config import load_config
from app.repositories import (
    get_canonical_page_id, get_model_config, get_post, get_video_analysis,
    list_posts, parse_video_analysis_content, save_video_analysis,
    update_video_analysis, update_video_analysis_pushed,
)
from app.services.ai_reply import AIReplyService

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def _parse_fb_timestamp(ts: str) -> int:
    if not ts:
        return 0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


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

    # Step 1: Get video source URL
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(
                f"{config.graph_base_url}/{video_id}",
                params={"fields": "source,permalink_url,description,length", "access_token": config.page_access_token},
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
    del video_bytes

    ai_service = AIReplyService(config)
    try:
        result = await ai_service.analyze_video(b64)
    except Exception as exc:
        logger.error("[analyze] LLM analysis failed for post=%s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"AI 分析失败: {exc}")

    title = (post.get("message") or "")[:200]
    post_time = _parse_fb_timestamp(post.get("created_time", ""))
    try:
        save_video_analysis(post_id, title, result, post_time)
    except Exception as exc:
        logger.warning("[analyze] Failed to save analysis result: %s", exc)

    return {"status": "success", "result": result, "cached": False}


@router.post("/posts/{post_id}/analyze")
async def analyze_post_video(post_id: str, force: bool = False):
    from app.task import create_task, update_task as _ut, STATUS_SUCCESS as _S, STATUS_FAILED as _F

    task_key = f"video_analysis_{post_id}"

    if not force:
        cached = get_video_analysis(post_id)
        if cached:
            return {"status": "success", "result": cached["content"], "cached": True}

    create_task(task_key, f"视频分析 {post_id}")
    _ut(task_key, status="running")

    try:
        result = await _do_analyze(post_id, force, task_key)
        _ut(task_key, status=_S)
        return result
    except Exception:
        _ut(task_key, status=_F, message="视频分析失败")
        raise


@router.put("/posts/{post_id}/analyze")
async def update_post_video_analysis(post_id: str, payload: dict[str, str]):
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


@router.post("/video/batch-analyze")
async def batch_analyze_videos():
    from app.task import create_task, update_task, get_task, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED

    config = load_config()
    page_id = get_canonical_page_id(config.page_id)
    posts = list_posts(page_id=page_id, limit=200)
    video_posts = [p for p in posts if p.get("type") == "video"]
    unanalyzed = [p for p in video_posts if not get_video_analysis(p["id"])]

    if not unanalyzed:
        return {"status": "success", "total": 0, "msg": "没有需要分析的视频"}

    task_key = "batch_video_analysis"
    total = len(unanalyzed)
    create_task(task_key, "批量视频分析")
    update_task(task_key, status="running", message=f"开始批量分析 {total} 个视频...", progress=0,
                result={"total": total, "completed": 0})

    results = {"success": 0, "failed": 0, "errors": []}
    for i, post in enumerate(unanalyzed):
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
    content = f"{parsed['location']}\n{parsed['behavior']}\n{parsed['environment']}" if parsed else content_raw

    post = get_post(post_id)
    title = (post.get("message") or "")[:150] if post else ""
    post_time = _parse_fb_timestamp(post.get("created_time", "")) if post else 0

    payload = {"postsId": post_id, "postTime": post_time, "title": title, "content": content}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                push_url, json=payload,
                headers={"Content-Type": "application/json", "Cookie": f"mgr_token={push_token}"},
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.error("[push] Failed to push post=%s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"推送失败: {exc}")

    now = datetime.now(timezone.utc).isoformat()
    update_video_analysis_pushed(post_id, now)
    return {"status": "success", "pushed_at": now}
