"""Tests for ChatSyncService — conversation and message synchronization."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def chat_config(setup_db):
    from app.config import AppConfig
    from app.repositories import create_account
    from tests.factories import make_account

    create_account(**make_account(page_id="123456789", token="EAA_tok",
                                   verify="v", is_active=1))
    return AppConfig(
        account_id=1, account_name="Test",
        page_access_token="EAA_tok", verify_token="v",
        page_id="123456789",
    )


class TestFilterMessageContent:
    def test_text_message(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        msg = {"message": "Hello world"}
        assert svc._filter_message_content(msg) == "Hello world"

    def test_sticker_message(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        msg = {"sticker": "12345"}
        assert "[表情]" in svc._filter_message_content(msg)

    def test_photo_attachment(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        msg = {"attachments": {"data": [{"type": "photo"}]}}
        content = svc._filter_message_content(msg)
        # _filter_message_content maps photo→[图片], other→[文件]
        assert "[图片]" in content or "[文件]" in content

    def test_video_attachment(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        msg = {"attachments": {"data": [{"type": "video"}]}}
        content = svc._filter_message_content(msg)
        assert "[文件]" in content

    def test_audio_attachment(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        msg = {"attachments": {"data": [{"type": "audio"}]}}
        content = svc._filter_message_content(msg)
        assert "[文件]" in content

    def test_file_attachment(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        msg = {"attachments": {"data": [{"type": "fallback"}]}}
        content = svc._filter_message_content(msg)
        assert "[文件]" in content


class TestIsoToUnix:
    def test_conversion(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        ts = svc._iso_to_unix("2025-06-01T10:00:00+0000")
        assert ts > 0
        assert isinstance(ts, int)

    def test_invalid_format(self, chat_config):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        assert svc._iso_to_unix("not a date") == 0


class TestChatSyncBasic:
    @pytest.mark.asyncio
    async def test_sync_messages_for_conversation_empty(self, chat_config, httpx_mock):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService
        from app.repositories import upsert_page_conversation, upsert_page_profile
        from tests.factories import make_facebook_page_profile

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        upsert_page_conversation("conv1", "123456789",
                                 "2025-06-01T10:00:00+0000", 0, "[]")

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        # Mock: no messages
        httpx_mock.add_response(
            json={"data": [], "paging": {}},
        )
        count = await svc._sync_messages_for_conversation("conv1", full_sync=True)
        assert count == 0

    @pytest.mark.asyncio
    async def test_sync_messages_with_data(self, chat_config, httpx_mock):
        from app.services.chat_sync import ChatSyncService
        from app.services.facebook import FacebookService
        from app.repositories import upsert_page_conversation, upsert_page_profile
        from tests.factories import make_facebook_page_profile

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        upsert_page_conversation("conv2", "123456789",
                                 "2025-06-01T10:00:00+0000", 0, "[]")

        fb = FacebookService(chat_config)
        svc = ChatSyncService(fb)

        httpx_mock.add_response(
            json={
                "data": [
                    {"id": "msg1", "message": "Hi!",
                     "from": {"id": "user1", "name": "Alice"},
                     "created_time": "2025-06-01T10:00:00+0000"},
                ],
                "paging": {},
            },
        )
        count = await svc._sync_messages_for_conversation("conv2", full_sync=True)
        assert count == 1
        from app.repositories import check_message_exists
        assert check_message_exists("msg1")