"""Tests for AIReplyService — LLM integration with httpx mocking."""
import pytest


@pytest.fixture
def ai_config(setup_db):
    from app.config import AppConfig
    from app.repositories import create_account, upsert_model_config
    from tests.factories import make_account, make_model_config

    create_account(**make_account(page_id="123456789", token="tok",
                                   verify="v", is_active=1))
    upsert_model_config(**make_model_config(
        reply_url="https://api.openai.com/v1",
        reply_key="sk-test",
        reply_model="gpt-4",
        video_url="https://api.openai.com/v1",
        video_key="sk-test-video",
        video_model="gpt-4-vision",
    ))

    return AppConfig(
        account_id=1, account_name="Test", page_access_token="tok",
        verify_token="v", page_id="123456789",
        reply_api_base_url="https://api.openai.com/v1",
        reply_api_key="sk-test",
        reply_model="gpt-4",
        video_api_base_url="https://api.openai.com/v1",
        video_api_key="sk-test-video",
        video_model="gpt-4-vision",
    )


@pytest.fixture
def ai(ai_config):
    from app.services.ai_reply import AIReplyService

    return AIReplyService(ai_config)


class TestAIReplyService:
    @pytest.mark.asyncio
    async def test_generate_reply_success(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "Great post!"}}]},
        )
        reply = await ai.generate_reply(
            page_name="TestPage", post_message="Check this out",
            comment_message="Awesome!", comment_author="User1",
        )
        assert reply == "Great post!"

    @pytest.mark.asyncio
    async def test_generate_reply_disabled_config(self):
        from app.config import AppConfig
        from app.services.ai_reply import AIReplyService

        cfg = AppConfig(
            account_id=1, account_name="Test", page_access_token="tok",
            verify_token="v", page_id="123",
        )
        svc = AIReplyService(cfg)
        with pytest.raises(RuntimeError):
            await svc.generate_reply(page_name="X", post_message="X",
                                     comment_message="X", comment_author="X")

    @pytest.mark.asyncio
    async def test_test_reply_connection_success(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "hi"}}]},
        )
        result = await ai.test_reply_connection()
        assert "成功" in result

    @pytest.mark.asyncio
    async def test_test_reply_connection_failure(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            status_code=401,
        )
        with pytest.raises(RuntimeError):
            await ai.test_reply_connection()

    @pytest.mark.asyncio
    async def test_test_video_connection_success(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "hi"}}]},
        )
        result = await ai.test_video_connection()
        assert "成功" in result

    @pytest.mark.asyncio
    async def test_test_connection_backward_compat(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "hi"}}]},
        )
        result = await ai.test_connection()
        assert "成功" in result

    @pytest.mark.asyncio
    async def test_score_comments_success(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={
                "choices": [{"message": {"content": '[{"id": "c1", "score": 85}, {"id": "c2", "score": 60}]'}}]
            },
        )
        comments = [
            {"id": "c1", "message": "Great!"},
            {"id": "c2", "message": "ok"},
        ]
        scores = await ai.score_comments(post_message="Test post",
                                         video_analysis="", comments=comments)
        assert len(scores) == 2
        assert scores[0]["score"] == 85
        assert scores[1]["score"] == 60

    @pytest.mark.asyncio
    async def test_score_comments_strips_code_fence(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={
                "choices": [{"message": {"content": '```json\n[{"id": "c1", "score": 90}]\n```'}}]
            },
        )
        comments = [{"id": "c1", "message": "Test"}]
        scores = await ai.score_comments(post_message="P", video_analysis="",
                                         comments=comments)
        assert scores[0]["score"] == 90

    @pytest.mark.asyncio
    async def test_score_comments_fallback_on_error(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            status_code=500,
        )
        comments = [{"id": "c1", "message": "Test"}]
        scores = await ai.score_comments(post_message="P", video_analysis="",
                                         comments=comments)
        assert scores[0]["score"] == 50

    @pytest.mark.asyncio
    async def test_score_comments_empty_list(self, ai, httpx_mock):
        scores = await ai.score_comments(post_message="P", video_analysis="",
                                         comments=[])
        assert scores == []

    @pytest.mark.asyncio
    async def test_generate_reply_retry_on_5xx(self, ai, httpx_mock):
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            status_code=500,
        )
        httpx_mock.add_response(
            url="https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "Retry worked!"}}]},
        )
        reply = await ai.generate_reply(
            page_name="P", post_message="PM",
            comment_message="CM", comment_author="A",
        )
        assert reply == "Retry worked!"


class TestChatCompletionsURL:
    def test_appends_chat_completions(self, ai):
        result = ai._chat_completions_url("https://api.test.com")
        assert result == "https://api.test.com/chat/completions"

    def test_already_has_chat_completions(self, ai):
        result = ai._chat_completions_url("https://api.test.com/chat/completions")
        assert result == "https://api.test.com/chat/completions"