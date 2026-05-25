from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from app.config import AppConfig, PROJECT_ROOT

_PROMPT_DIR = PROJECT_ROOT / "prompts"
_DEFAULT_TEMPLATE = (
    "主页名称: {{ page_name or '未提供' }}\n"
    "帖子内容: {{ post_message or '未提供' }}\n"
    "{% if video_analysis %}视频分析: {{ video_analysis }}\n{% endif %}"
    "评论用户: {{ author_name or '匿名用户' }}\n"
    "评论内容: {{ comment_message or '（空）' }}\n"
    "{% if parent_comment_message %}被回复的原评论: {{ parent_comment_message }}\n{% endif %}"
    "你的人设如下：\n"
    "- 35 岁男性，白手起家的投资人，曾创立并出售科技公司，已实现财务自由。\n"
    "- 气质自信、沉稳、有掌控力；生活重心是投资、旅行、健身与高质量生活。\n"
    "- 爱好包括健身训练、探索旅行、户外活动、偶尔下厨、发掘美食餐厅。\n"
    "- 给人感觉冷静、可靠、有吸引力，能让人安心。\n\n"
    "性格与互动风格：\n"
    "- 成熟、稳定、略带主导感；自信但不张扬。\n"
    "- 保护欲、观察力强，偶尔带一点俏皮和轻微调侃。\n"
    "- 关心对方时温暖但不黏人，不刻意讨好。\n"
    "- 说话像真实男性，不要像 AI 助手，不要说教。\n\n"
    "表达要求：\n"
    "- 回复自然、口语化、直接，避免生硬或官腔。\n"
    "- 大多数回复控制在 1-3 句，简洁但有温度。\n"
    "- 不要输出解释、分析、步骤、标题，不要加引号，不要前缀。\n"
    "- 可以轻微调情和玩笑，但保持分寸、自然推进，不要突兀。\n\n"
    "语言适配规则（必须遵守）：\n"
    "- 回复语言必须与“评论内容”的主要语言保持一致。\n"
    "- 如果“评论内容”很短或语言不明确，则参考“被回复的原评论”的主要语言。\n"
    "- 如果仍无法判断，则默认使用简体中文。\n"
    "- 不要额外说明你在切换语言，直接用对应语言回复。\n\n"
    "任务：\n"
    "请直接输出一条适合发在 Facebook 评论区的回复。"
)


def _build_user_prompt(
    *,
    page_name: str,
    post_message: str,
    comment_message: str,
    author_name: str,
    parent_comment_message: str = "",
    previous_replies: list[dict[str, Any]] | None = None,
    video_analysis: str = "",
    template_name: str = "reply_prompt.j2",
) -> str:
    try:
        env = Environment(
            loader=FileSystemLoader(str(_PROMPT_DIR)),
            autoescape=False,
            keep_trailing_newline=True,
        )
        template = env.get_template(template_name)
    except (TemplateNotFound, OSError):
        from jinja2 import Template
        template = Template(_DEFAULT_TEMPLATE)

    return template.render(
        page_name=page_name,
        post_message=post_message,
        comment_message=comment_message,
        author_name=author_name,
        parent_comment_message=parent_comment_message,
        previous_replies=previous_replies or [],
        video_analysis=video_analysis,
    ).strip()


class AIReplyService:
    def __init__(self, config: AppConfig):
        self.config = config

    def _chat_completions_url(self) -> str:
        base_url = self.config.ai_api_base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _looks_like_unsupported_param(self, detail: str) -> bool:
        text = (detail or "").lower()
        markers = [
            "unknown parameter",
            "unknown field",
            "unsupported",
            "unexpected field",
            "extra inputs",
            "not permitted",
            "unrecognized",
            "invalid parameter",
        ]
        return any(m in text for m in markers)

    async def generate_reply(
        self,
        *,
        page_name: str,
        post_message: str,
        comment_message: str,
        comment_author: str,
        parent_comment_message: str = "",
        previous_replies: list[dict[str, Any]] | None = None,
        video_analysis: str = "",
    ) -> str:
        if not self.config.ai_enabled:
            raise RuntimeError("AI 配置不完整，请先在 config.json 中填写 AI_API_BASE_URL、AI_API_KEY 和 AI_MODEL")

        user_content = _build_user_prompt(
            page_name=page_name,
            post_message=post_message,
            comment_message=comment_message,
            author_name=comment_author,
            parent_comment_message=parent_comment_message,
            previous_replies=previous_replies,
            video_analysis=video_analysis,
            template_name=self.config.prompt_template,
        )

        payload_base: dict[str, Any] = {
            "model": self.config.ai_model,
            "temperature": 0.4,
            "max_tokens": 180,
            "stream": False,
            "messages": [
                {"role": "user", "content": user_content},
            ],
        }

        # Prefer non-thinking mode for faster comment replies.
        payload_fast = {
            **payload_base,
            "enable_thinking": False,
            "reasoning": {"effort": "none"},
        }

        headers = {
            "Authorization": f"Bearer {self.config.ai_api_key}",
            "Content-Type": "application/json",
        }

        response: httpx.Response | None = None
        last_exc: Exception | None = None

        async with httpx.AsyncClient(timeout=35.0) as client:
            for attempt in range(3):
                try:
                    response = await client.post(self._chat_completions_url(), headers=headers, json=payload_fast)

                    if response.status_code >= 400:
                        detail = response.text
                        try:
                            detail = response.json().get("error", {}).get("message", detail)
                        except ValueError:
                            pass

                        # Some OpenAI-compatible providers reject custom reasoning fields.
                        if self._looks_like_unsupported_param(detail):
                            response = await client.post(self._chat_completions_url(), headers=headers, json=payload_base)
                    
                    if response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(1.0 * (2 ** attempt))
                            continue
                    break
                except httpx.RequestError as exc:
                    last_exc = exc
                    if attempt < 2:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                        continue
                    break

        if response is None:
            raise RuntimeError(f"AI 接口请求失败: {last_exc or '未收到响应'}")

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
            raise RuntimeError(detail)

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("AI 接口未返回可用内容")

        content = choices[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("AI 接口返回了空内容")
        return content

    async def test_connection(self) -> str:
        """Tests the connection to the LLM with a simple prompt."""
        if not self.config.ai_api_base_url or not self.config.ai_api_key or not self.config.ai_model:
            raise RuntimeError("请先填写 AI_API_BASE_URL、AI_API_KEY 和 AI_MODEL")

        payload = {
            "model": self.config.ai_model,
            "messages": [
                {"role": "user", "content": "hi"},
            ],
            "max_tokens": 5,
        }

        headers = {
            "Authorization": f"Bearer {self.config.ai_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(self._chat_completions_url(), headers=headers, json=payload)
                if response.status_code >= 400:
                    detail = response.text
                    try:
                        detail = response.json().get("error", {}).get("message", detail)
                    except ValueError:
                        pass
                    raise RuntimeError(f"连接失败 ({response.status_code}): {detail}")

                return "连接成功！AI 已响应。"
            except httpx.RequestError as exc:
                raise RuntimeError(f"网络请求失败: {exc}") from exc

    async def screen_comment(
        self,
        *,
        comment_message: str,
        comment_author: str,
    ) -> bool:
        """Lightweight LLM call to decide if a comment is worth replying to.

        Returns True if the comment has conversion potential, False to skip.
        Fails open (returns True on any error).
        """
        if not self.config.ai_enabled:
            return True

        prompt = (
            "你是一个评论筛选助手。判断以下Facebook评论是否有较高的互动转化潜力（用户可能点击私聊链接）。\n\n"
            "回复规则：\n"
            "- 以下情况回复 SKIP：纯表情、垃圾广告、完全无关内容、只有一个字（如\"好\"\"ok\"\"nice\"）\n"
            "- 以下情况回复 REPLY：有情感表达、提出问题、表达兴趣、调情/互动、任何有对话潜力的评论\n\n"
            "只回复一个词：REPLY 或 SKIP\n\n"
            f"用户 {comment_author} 说：{comment_message}"
        )

        payload = {
            "model": self.config.ai_model,
            "temperature": 0.1,
            "max_tokens": 10,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }

        headers = {
            "Authorization": f"Bearer {self.config.ai_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._chat_completions_url(), headers=headers, json=payload)
                if response.status_code >= 400:
                    return True  # fail-open
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().upper()
                return "REPLY" in content
        except Exception:
            return True  # fail-open

    async def score_comments(
        self,
        *,
        post_message: str,
        video_analysis: str = "",
        comments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Batch score comments for target user potential (0-100).

        Returns list of {id, score}, sorted by score descending.
        Fails open: returns all comments with score=50 on any error.
        """
        if not self.config.ai_enabled or not comments:
            return [{"id": c["id"], "score": 50} for c in comments]

        comment_lines = []
        for c in comments:
            comment_lines.append(
                f"[id: {c['id']}] {c.get('author_name', '匿名用户')}: {c.get('message', '')}"
            )
        comment_text = "\n".join(comment_lines)

        prompt = (
            "你是一个评论价值评估助手。以下是帖子内容和所有待评估的评论。\n"
            "请对每条评论进行评分（0-100），判断该用户是否可能点击私聊链接、产生深度互动。\n\n"
            f"帖子内容: {post_message or '无'}\n"
            + (f"视频分析: {video_analysis}\n" if video_analysis else "") +
            "\n"
            "评分标准（0-100）:\n"
            "- 90-100: 强烈互动意愿、主动调情、表达情感、询问私人问题、明显想进一步交流\n"
            "- 70-89: 有对话潜力、表达兴趣或好奇、积极互动\n"
            "- 50-69: 普通评论、简短互动、社交礼貌\n"
            "- 30-49: 简单表情、单字回复、低信息量\n"
            "- 0-29: 垃圾广告、完全无关内容\n\n"
            f"待评估评论:\n{comment_text}\n\n"
            "请以 JSON 数组格式返回每条评论的评分:\n"
            '[{"id": "评论id", "score": 85}, ...]\n'
            "只输出 JSON 数组，不要包含其他内容。"
        )

        payload = {
            "model": self.config.ai_model,
            "temperature": 0.1,
            "max_tokens": 500,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }

        headers = {
            "Authorization": f"Bearer {self.config.ai_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(self._chat_completions_url(), headers=headers, json=payload)
                if response.status_code >= 400:
                    return [{"id": c["id"], "score": 50} for c in comments]
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            # Parse JSON array from response
            scored = json.loads(content)
            if not isinstance(scored, list):
                return [{"id": c["id"], "score": 50} for c in comments]

            # Build lookup and preserve order
            result = []
            for item in scored:
                if isinstance(item, dict) and "id" in item and "score" in item:
                    result.append({"id": str(item["id"]), "score": int(item["score"])})

            # Fill in any missing comments with score=50
            scored_ids = {r["id"] for r in result}
            for c in comments:
                if c["id"] not in scored_ids:
                    result.append({"id": c["id"], "score": 50})

            result.sort(key=lambda x: x["score"], reverse=True)
            return result
        except Exception:
            return [{"id": c["id"], "score": 50} for c in comments]

    async def analyze_video(self, video_base64: str) -> str:
        """Analyze a video using LLM with base64-encoded video data."""
        if not self.config.ai_enabled:
            raise RuntimeError("AI 配置不完整，请先在设置中填写 AI 配置")

        data_url = f"data:video/mp4;base64,{video_base64}"

        prompt = (
            "你是一个专业的视频内容分析师。请仔细观看这段视频，重点关注画面中的背景景物、"
            "环境特征、建筑风格、植被、天气、光线等视觉细节。\n\n"
            "请用中文以 JSON 格式返回以下三个字段：\n\n"
            '1. "location"：推测视频拍摄的具体地点，格式为"国家 - 省/州 - 城市"。'
            "只给出结论，不要包含分析推理过程。\n"
            '2. "behavior"：视频中人物的具体行为，包括动作、穿着、表情、互动方式等，2-3 句话。\n'
            '3. "environment"：视频中的整体环境氛围，包括背景中出现的地标建筑、自然景观、'
            "交通工具、人群密度等，2-3 句话。\n\n"
            "请基于你观察到的具体画面细节进行分析，不要凭空猜测。"
        )

        model = self.config.video_ai_model or self.config.ai_model
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": data_url,
                                "fps": 2,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            "max_tokens": 1600,
            "temperature": 0.2,
            "enable_thinking": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "video_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "location": {
                                "type": "string",
                                "description": "推测的拍摄地点，格式：国家 - 省/州 - 城市",
                            },
                            "behavior": {
                                "type": "string",
                                "description": "人物行为描述，2-3句话",
                            },
                            "environment": {
                                "type": "string",
                                "description": "场景环境描述，2-3句话",
                            },
                        },
                        "required": ["location", "behavior", "environment"],
                        "additionalProperties": False,
                    },
                },
            },
        }

        headers = {
            "Authorization": f"Bearer {self.config.ai_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(self._chat_completions_url(), headers=headers, json=payload)
            if response.status_code >= 400:
                detail = response.text
                try:
                    detail = response.json().get("error", {}).get("message", detail)
                except ValueError:
                    pass
                raise RuntimeError(f"视频分析失败 ({response.status_code}): {detail}")

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("AI 接口未返回可用内容")

        content = choices[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("AI 接口返回了空内容")

        # Validate JSON structure
        import json as _json
        try:
            parsed = _json.loads(content)
            for field in ("location", "behavior", "environment"):
                if field not in parsed or not str(parsed[field]).strip():
                    raise RuntimeError(f"AI 返回的 JSON 缺少必要字段: {field}")
        except _json.JSONDecodeError as e:
            raise RuntimeError(f"AI 返回的内容不是有效 JSON: {e}")

        return content
