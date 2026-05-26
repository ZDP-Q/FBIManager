"""Tests for WebhookService — Facebook webhook event processing."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def wh_config(setup_db):
    from app.config import AppConfig
    from app.repositories import create_account, upsert_page_profile, upsert_model_config
    from tests.factories import make_account, make_facebook_page_profile, make_model_config

    upsert_page_profile(make_facebook_page_profile(page_id="123456789", name="TestPage"))
    create_account(**make_account(page_id="123456789", token="tok",
                                   verify="verify_me", is_active=1))
    upsert_model_config(**make_model_config())

    return AppConfig(
        account_id=1, account_name="Test",
        page_access_token="tok", verify_token="verify_me",
        page_id="123456789",
        reply_api_base_url="https://api.test.com/v1",
        reply_api_key="sk-test", reply_model="gpt-4",
    )


class TestWebhookPayloadProcessing:
    @pytest.mark.asyncio
    async def test_non_page_object_returns_zero(self, wh_config):
        from app.services.webhook import WebhookService

        svc = WebhookService(wh_config)
        result = await svc.process_payload({"object": "user"})
        assert result == {"processed": 0, "replied": 0, "skipped": 0}

    @pytest.mark.asyncio
    async def test_empty_entry(self, wh_config):
        from app.services.webhook import WebhookService

        svc = WebhookService(wh_config)
        result = await svc.process_payload({"object": "page", "entry": []})
        assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_non_comment_change_skipped(self, wh_config):
        from app.services.webhook import WebhookService

        svc = WebhookService(wh_config)
        payload = {
            "object": "page",
            "entry": [{
                "id": "123456789",
                "time": 1234567890,
                "changes": [{
                    "field": "feed",
                    "value": {"item": "post", "verb": "add"},
                }],
            }],
        }
        result = await svc.process_payload(payload)
        assert result == {"processed": 0, "replied": 0, "skipped": 0}

    @pytest.mark.asyncio
    async def test_own_comment_skipped(self, wh_config):
        from app.services.webhook import WebhookService
        from app.repositories import upsert_post
        from tests.factories import make_facebook_post

        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))

        svc = WebhookService(wh_config)

        # Mock the async Facebook and AI calls
        svc.facebook.fetch_post = AsyncMock(return_value={
            "message": "Test post",
            "created_time": "2025-01-01T00:00:00+0000",
        })
        svc.ai.generate_reply = AsyncMock(return_value="Should not be called")

        payload = {
            "object": "page",
            "entry": [{
                "id": "123456789",
                "time": 1234567890,
                "changes": [{
                    "field": "feed",
                    "value": {
                        "item": "comment",
                        "verb": "add",
                        "from": {"id": "123456789", "name": "TestPage"},
                        "post_id": "123456789_001",
                        "comment_id": "own_comment_1",
                        "message": "My own comment",
                    },
                }],
            }],
        }
        result = await svc.process_payload(payload)
        # Own comment should be skipped, not replied
        assert result["skipped"] >= 1
        svc.ai.generate_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_comment_triggers_reply(self, wh_config):
        from app.services.webhook import WebhookService
        from app.repositories import upsert_post
        from tests.factories import make_facebook_post

        upsert_post("123456789", make_facebook_post(post_id="123456789_001",
                                                      message="Test post"))

        svc = WebhookService(wh_config)
        svc.facebook.fetch_post = AsyncMock(return_value={
            "message": "Test post",
            "created_time": "2025-01-01T00:00:00+0000",
        })
        svc.facebook.send_reply = AsyncMock(return_value={"id": "reply_1"})
        svc.ai.generate_reply = AsyncMock(return_value="Auto reply!")

        payload = {
            "object": "page",
            "entry": [{
                "id": "123456789",
                "time": 1234567890,
                "changes": [{
                    "field": "feed",
                    "value": {
                        "item": "comment",
                        "verb": "add",
                        "from": {"id": "user99", "name": "User"},
                        "post_id": "123456789_001",
                        "comment_id": "external_comment",
                        "message": "Nice post!",
                    },
                }],
            }],
        }
        result = await svc.process_payload(payload)
        assert result["replied"] >= 1
        svc.facebook.send_reply.assert_called_once()