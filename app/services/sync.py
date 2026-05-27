from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import AppConfig
from app.repositories import replace_comments_for_post, upsert_page_profile, upsert_post, list_posts, get_canonical_page_id
from app.services.facebook import FacebookService
from app.services.attachments import download_comment_attachments
from app.task import create_task, update_task, get_task, is_task_running, STATUS_SUCCESS, STATUS_FAILED, STATUS_RUNNING


logger = logging.getLogger("uvicorn.error")


class SyncService:
    def __init__(self, config: AppConfig):
        self.facebook = FacebookService(config)
        self.config = config

    async def sync_all(self, *, post_limit: int = 6, since: str = "", until: str = "", all_posts: bool = False) -> dict[str, Any]:
        """Wrapper around sync_all_gen for backward compatibility."""
        final_result = {}
        async for step in self.sync_all_gen(post_limit=post_limit, since=since, until=until, all_posts=all_posts):
            if "status" in step and step["status"] == "completed":
                final_result = step.get("result", {})
        return final_result

    async def sync_all_gen(self, *, post_limit: int = 6, since: str = "", until: str = "", all_posts: bool = False, sync_comments: bool = True):
        """Progress generator for SSE. It starts the background worker if not already running."""
        logger.info("[sync_all_gen] called with post_limit=%s, all_posts=%s, sync_comments=%s", post_limit, all_posts, sync_comments)
        if not is_task_running("post_sync"):
            logger.info("[sync_all_gen] creating task and spawning worker")
            create_task("post_sync", "帖子同步")
            asyncio.create_task(self._run_sync_worker(post_limit, since, until, all_posts, sync_comments))
            await asyncio.sleep(0.1)

        last_update = ""
        while True:
            task = get_task("post_sync")
            logger.info("[sync_all_gen] polled task: status=%s, updated_at=%s", task.get("status") if task else None, task.get("updated_at") if task else None)
            if not task:
                break

            updated = task.get("updated_at", "")
            if updated > last_update:
                logger.info("[sync_all_gen] yielding update")
                yield {"msg": task.get("message", ""), "percent": task.get("progress", 0),
                       "done": task["status"] in (STATUS_SUCCESS, STATUS_FAILED), "updated_at": updated}
                last_update = updated

            if task["status"] in (STATUS_SUCCESS, STATUS_FAILED):
                break
            await asyncio.sleep(1)

    async def _run_sync_worker(self, post_limit: int, since: str, until: str, all_posts: bool, sync_comments: bool = True):
        if self.config.page_id == "default-page":
            logger.warning("[sync] Skipping sync for 'default-page'.")
            update_task("post_sync", status=STATUS_SUCCESS, message="跳过默认页面", progress=0)
            return

        try:
            from app.repositories import get_page_profile
            # 优先使用本地已有的主页信息，不再强制每次同步都请求 Facebook
            profile = get_page_profile(page_id=self.config.page_id)
            if not profile:
                update_task("post_sync", message="正在获取主页基本信息...", progress=5)
                profile = await self.facebook.fetch_page_profile()
                upsert_page_profile(profile)

            canonical_page_id = str(profile.get("page_id") or profile.get("id") or "")
            normalized_all_posts = all_posts or post_limit <= 0

            status_msg = f"正在获取帖子列表 (limit={post_limit if not normalized_all_posts else '全部'})..."
            update_task("post_sync", message=status_msg, progress=15)
            
            raw_posts, next_cursor = await self._fetch_posts_for_sync(
                canonical_page_id=canonical_page_id,
                post_limit=post_limit,
                since=since,
                until=until,
                all_posts=normalized_all_posts,
            )
            
            posts = []
            for post in raw_posts:
                if self._is_post_from_current_page(post, canonical_page_id):
                    posts.append(post)

            total_posts = len(posts)
            if not posts:
                update_task("post_sync", status=STATUS_SUCCESS, message="同步完成，未发现新帖子", progress=100)
                return

            status_msg = f"发现 {total_posts} 篇帖子，开始同步媒体信息{'和评论' if sync_comments else ''}..."
            update_task("post_sync", message=status_msg, progress=25)

            synced_comment_count = 0
            batch_size = 5
            processed_count = 0

            for i in range(0, total_posts, batch_size):
                # 检查是否被用户手动停止
                task = get_task("post_sync")
                if task and task["status"] in (STATUS_SUCCESS, STATUS_FAILED):
                    logger.info("[sync] sync stopped by user")
                    return

                batch = posts[i : i + batch_size]
                await asyncio.gather(*[self._sync_post_media(canonical_page_id, p) for p in batch])
                if sync_comments:
                    counts = await asyncio.gather(*[self._sync_post_comments(p) for p in batch])
                    synced_comment_count += sum(counts)

                processed_count += len(batch)
                percent = 25 + int((processed_count / total_posts) * 70)
                status_msg = f"已处理 {processed_count}/{total_posts} 篇帖子..."
                update_task("post_sync", message=status_msg, progress=percent)

            update_task("post_sync", status=STATUS_SUCCESS, message="同步完成！", progress=100,
                        result={
                            "page_id": canonical_page_id,
                            "post_count": total_posts,
                            "comment_count": synced_comment_count,
                            "next_cursor": next_cursor,
                            "all_posts": normalized_all_posts,
                        })
        except Exception as e:
            logger.error("[sync] Background worker failed: %s", e, exc_info=True)
            update_task("post_sync", status=STATUS_FAILED, message=f"同步失败: {str(e)}", error=str(e))
        finally:
            await self.facebook.close()

    async def sync_post(self, post_id: str) -> dict[str, Any]:
        if self.config.page_id == "default-page":
            logger.warning("[sync_post] Skipping sync for 'default-page' as it is a placeholder.")
            return {"status": "skipped", "reason": "default-page"}

        logger.info("[sync] start syncing single post=%s", post_id)
        try:
            from app.repositories import get_page_profile
            profile = get_page_profile(page_id=self.config.page_id)
            if not profile:
                profile = await self.facebook.fetch_page_profile()
                upsert_page_profile(profile)

            canonical_page_id = str(profile.get("page_id") or profile.get("id") or "")

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
        finally:
            await self.facebook.close()

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
            all_comments: list[dict[str, Any]] = []
            after_cursor = ""
            page = 0
            while True:
                comments, cursors = await self.facebook.fetch_comments_for_post(
                    post_id, limit=100, max_depth=3, after=after_cursor
                )
                if not comments:
                    break
                all_comments.extend(comments)
                page += 1
                after_cursor = cursors.get("after", "")
                if not after_cursor:
                    break

            replace_comments_for_post(post_id, all_comments)
            count = sum(self._count_comment_tree(c) for c in all_comments)

            # Download attachments

            async def _walk_attachments(cs: list[dict[str, Any]]) -> int:
                count = 0
                for c in cs:
                    count += await download_comment_attachments(c, self.facebook)
                    for reply in c.get("replies", {}).get("data", []):
                        count += await _walk_attachments([reply])
                return count

            attached = await _walk_attachments(all_comments)

            logger.info("[sync] comments synced for post=%s pages=%s total=%s attachments=%s",
                        post_id, page, count, attached)
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
