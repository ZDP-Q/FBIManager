from __future__ import annotations

from typing import Any

from app.config import AppConfig
from app.repositories import get_page_profile
from app.services.ai_reply import AIReplyService
from app.services.facebook import FacebookService


class WebhookService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.facebook = FacebookService(config)
        self.ai = AIReplyService(config)

    async def process_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        processed = 0
        replied = 0
        skipped = 0

        if payload.get("object") != "page":
            return {"processed": 0, "replied": 0, "skipped": 0}

        page_profile = get_page_profile(page_id=self.config.page_id) or {}

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                item = value.get("item")
                verb = value.get("verb")

                if item != "comment" or verb != "add":
                    continue

                processed += 1
                comment_id = value.get("comment_id")
                comment_message = (value.get("message") or "").strip()
                post_id = value.get("post_id")
                sender_id = value.get("from", {}).get("id")
                sender_name = value.get("from", {}).get("name", "匿名用户")

                if not comment_id or not comment_message:
                    skipped += 1
                    continue

                # 防死循环：跳过主页自己的评论
                if sender_id and sender_id == self.config.page_id:
                    skipped += 1
                    continue

                post_message = ""
                if post_id:
                    try:
                        post_data = await self.facebook.fetch_post(post_id)
                        post_message = post_data.get("message", "")
                    except Exception:
                        # 获取帖子内容失败不阻断回复流程
                        post_message = ""

                try:
                    ai_text = await self.ai.generate_reply(
                        page_name=page_profile.get("name", ""),
                        post_message=post_message,
                        comment_message=comment_message,
                        comment_author=sender_name,
                    )
                    await self.facebook.send_reply(comment_id, ai_text)
                    replied += 1
                except Exception:
                    skipped += 1

        return {"processed": processed, "replied": replied, "skipped": skipped}
