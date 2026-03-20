from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import AppConfig
from app.repositories import replace_comments_for_post, upsert_insights, upsert_page_profile, upsert_post, list_posts, get_canonical_page_id
from app.services.facebook import FacebookService


logger = logging.getLogger("uvicorn.error")


class SyncService:
    def __init__(self, config: AppConfig):
        self.facebook = FacebookService(config)
        self.config = config

    async def sync_all(self, *, post_limit: int = 20, since: str = "", until: str = "", all_posts: bool = False) -> dict[str, Any]:
        logger.info("[sync] start syncing page profile")
        profile = await self.facebook.fetch_page_profile()
        upsert_page_profile(profile)
        
        # 核心修复：使用 Facebook 返回的官方数字 ID 进行后续操作
        canonical_page_id = str(profile.get("id", ""))
        logger.info("[sync] page profile synced: page_id=%s (canonical=%s) name=%s", 
                    self.config.page_id, canonical_page_id, profile.get("name", ""))

        normalized_all_posts = all_posts or post_limit <= 0
        logger.info(
            "[sync] start syncing posts (limit=%s, all_posts=%s, since=%s, until=%s)",
            post_limit,
            normalized_all_posts,
            since or "auto",
            until or "auto",
        )

        raw_posts, next_cursor = await self._fetch_posts_for_sync(
            canonical_page_id=canonical_page_id,
            post_limit=post_limit,
            since=since,
            until=until,
            all_posts=normalized_all_posts,
        )
        
        # 使用规范化后的 ID 进行过滤
        posts = []
        for post in raw_posts:
            is_valid = self._is_post_from_current_page(post, canonical_page_id)
            post_id = post.get("id")
            from_info = post.get("from", {})
            if is_valid:
                posts.append(post)
                logger.debug("[sync] accepted post: id=%s from=%s", post_id, from_info)
            else:
                logger.info("[sync] filtered out post: id=%s from=%s (reason: source mismatch with canonical=%s)", 
                            post_id, from_info, canonical_page_id)
        
        # 如果过滤后一个都不剩，但在过滤前是有数据的，强制记录一条警告
        if raw_posts and not posts:
            logger.warning("[sync] all %s fetched posts were filtered out! Please check if canonical_page_id=%s is correct.", 
                           len(raw_posts), canonical_page_id)

        if posts:
            await self._run_in_batches(
                [self._sync_post_media(canonical_page_id, p) for p in posts],
                batch_size=8,
            )
        logger.info("[sync] posts synced: %s", len(posts))

        synced_comment_count = 0
        if posts:
            counts = await self._run_in_batches(
                [self._sync_post_comments(p) for p in posts],
                batch_size=6,
            )
            synced_comment_count = sum(counts)

        logger.info("[sync] finished syncing comments: total (approx)=%s", synced_comment_count)

        return {
            "page_id": profile.get("id") or self.config.page_id,
            "post_count": len(posts),
            "comment_count": synced_comment_count,
            "next_cursor": next_cursor,
            "all_posts": normalized_all_posts,
        }

    async def sync_post(self, post_id: str) -> dict[str, Any]:
        logger.info("[sync] start syncing single post=%s", post_id)
        profile = await self.facebook.fetch_page_profile()
        upsert_page_profile(profile)
        canonical_page_id = str(profile.get("id", ""))

        post = await self.facebook.fetch_post(post_id)
        if not self._is_post_from_current_page(post, canonical_page_id):
            raise RuntimeError("目标帖子不属于当前主页，已拒绝同步")

        await self._sync_post_media(canonical_page_id, post)
        comment_count = await self._sync_post_comments(post)

        logger.info("[sync] single post synced: post=%s comments=%s", post_id, comment_count)
        return {
            "page_id": canonical_page_id or self.config.page_id,
            "post_id": post_id,
            "comment_count": comment_count,
        }

    async def sync_insights(self) -> dict[str, Any]:
        logger.info("[insights] start syncing page insights")
        page_metrics = 0
        canonical_page_id = get_canonical_page_id(self.config.page_id)
        try:
            page_data = await self.facebook.fetch_page_insights()
            upsert_insights(canonical_page_id, "page", page_data)
            page_metrics = len(page_data)
            logger.info("[insights] page insights synced: %s metrics", page_metrics)
        except Exception as exc:
            logger.warning("[insights] page insights failed: %s", exc)

        posts = list_posts(page_id=canonical_page_id)
        post_synced = 0
        for post in posts:
            post_id = post["id"]
            post_type = post.get("type", "") or ""
            try:
                media_info = await self.facebook.fetch_post_media_info(post_id)
                detected_type = media_info.get("type", "") or post_type
                video_id = media_info.get("target_id", "")

                if "video" in detected_type and video_id:
                    data = await self.facebook.fetch_video_insights(video_id)
                    upsert_insights(post_id, "video", data)
                    logger.info("[insights] video insights synced for post=%s video=%s: %s metrics", post_id, video_id, len(data))
                else:
                    data = await self.facebook.fetch_post_insights(post_id)
                    upsert_insights(post_id, "post", data)
                    logger.info("[insights] post insights synced for %s: %s metrics", post_id, len(data))
                post_synced += 1
            except Exception as exc:
                logger.warning("[insights] insights failed for post %s (%s): %s", post_id, post_type, exc)

        logger.info("[insights] finished: page_metrics=%s posts_synced=%s", page_metrics, post_synced)
        return {"page_metrics": page_metrics, "posts_synced": post_synced}

    def _is_post_from_current_page(self, post: dict[str, Any], canonical_page_id: str) -> bool:
        # 如果存在 from 字段且 id 明确，直接使用 from 来判断是不是主页自己的帖子
        from_id = post.get("from", {}).get("id")
        if from_id:
            if from_id == canonical_page_id or from_id == self.config.page_id:
                return True
            # 如果是有明确来源但不是本主页，则说明是其他人发在主页上的帖子（例如 feed 接口返回的）
            return False

        post_id = str(post.get("id", ""))
        # Facebook post ids usually look like "{page_id}_{post_id}".
        if "_" in post_id:
            prefix = post_id.split("_", 1)[0]
            # 匹配逻辑：帖子前缀匹配规范化 ID 或配置中的 ID
            return prefix == canonical_page_id or prefix == self.config.page_id
        
        # 若是其他格式ID（无下划线），由于现在我们请求了 'posts' 边缘，通常都是直接合法的帖子放行
        return True

    async def _fetch_posts_for_sync(
        self,
        *,
        canonical_page_id: str,
        post_limit: int,
        since: str,
        until: str,
        all_posts: bool,
    ) -> tuple[list[dict[str, Any]], str]:
        if not all_posts:
            result = await self.facebook.fetch_posts(
                limit=max(1, post_limit),
                since=since,
                until=until,
                page_id=canonical_page_id,
            )
            return result.get("data", []), result.get("paging", {}).get("cursors", {}).get("after", "")

        all_raw_posts: list[dict[str, Any]] = []
        cursor = ""
        while True:
            result = await self.facebook.fetch_posts(
                limit=100,
                since=since,
                until=until,
                after=cursor,
                page_id=canonical_page_id,
            )
            batch = result.get("data", [])
            all_raw_posts.extend(batch)

            paging = result.get("paging", {})
            cursor = paging.get("cursors", {}).get("after", "")
            has_next = bool(paging.get("next")) and bool(cursor)
            if not has_next or not batch:
                break

        return all_raw_posts, cursor

    async def _sync_post_media(self, canonical_page_id: str, post: dict[str, Any]) -> None:
        try:
            media_info = await self.facebook.fetch_post_media_info(post["id"])
            post["type"] = media_info.get("type", "")
            if media_info.get("target_id"):
                post["video_id"] = media_info["target_id"]
        except Exception as exc:
            logger.warning("[sync] failed to detect media type for post=%s: %s", post.get("id", ""), exc)
        upsert_post(canonical_page_id, post)

    async def _sync_post_comments(self, post: dict[str, Any]) -> int:
        post_id = post.get("id", "")
        try:
            comments = await self.facebook.fetch_comments_for_post(post_id, limit=200)
            replace_comments_for_post(post_id, comments)
            count = sum(self._count_comment_tree(c) for c in comments)
            logger.info("[sync] comments synced for post=%s top_level=%s total=%s", post_id, len(comments), count)
            return count
        except Exception as exc:
            logger.error("[sync] failed to sync comments for post=%s: %s", post_id, exc)
            return 0

    def _count_comment_tree(self, comment: dict[str, Any]) -> int:
        replies = comment.get("replies", {}).get("data", [])
        return 1 + sum(self._count_comment_tree(reply) for reply in replies)

    async def _run_in_batches(self, coroutines: list[Any], batch_size: int = 8) -> list[Any]:
        results: list[Any] = []
        for idx in range(0, len(coroutines), batch_size):
            batch = coroutines[idx : idx + batch_size]
            results.extend(await asyncio.gather(*batch))
        return results
