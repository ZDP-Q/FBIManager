"""Tests for SyncService — post/comment synchronization."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def sync_config(setup_db):
    from app.config import AppConfig
    from app.repositories import create_account, upsert_model_config
    from tests.factories import make_account, make_model_config

    create_account(**make_account(page_id="123456789", token="EAA_tok",
                                   verify="v", is_active=1))
    upsert_model_config(**make_model_config())

    return AppConfig(
        account_id=1, account_name="Test",
        page_access_token="EAA_tok",
        verify_token="v", page_id="123456789",
    )


class TestSyncPost:
    @pytest.mark.asyncio
    async def test_sync_post_success(self, sync_config, httpx_mock):
        from app.services.sync import SyncService
        from app.repositories import upsert_page_profile
        from tests.factories import make_facebook_page_profile

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))

        # Mock Facebook API calls: fetch_post, fetch_post_media_info, fetch_comments
        httpx_mock.add_response(
            json={"id": "123456789_001", "message": "Post content",
                  "created_time": "2025-06-01T10:00:00+0000",
                  "permalink_url": "https://fb.com/post"},
        )
        httpx_mock.add_response(json={"type": "photo"})
        httpx_mock.add_response(
            json={"data": [], "paging": {"cursors": {}}},
        )

        svc = SyncService(sync_config)
        result = await svc.sync_post("123456789_001")
        assert result["post_id"] == "123456789_001"
        assert "comment_count" in result

    @pytest.mark.asyncio
    async def test_sync_post_wrong_page_rejected(self, sync_config, httpx_mock):
        from app.services.sync import SyncService

        httpx_mock.add_response(
            json={"id": "123456789", "name": "Test Page", "fan_count": 1000,
                  "username": "test", "link": "", "category": "",
                  "picture": {"data": {"url": ""}}},
        )
        httpx_mock.add_response(
            json={"id": "999_001", "message": "Wrong page post",
                  "created_time": "", "permalink_url": "",
                  "from": {"id": "999"}},
        )

        svc = SyncService(sync_config)
        with pytest.raises(RuntimeError):
            await svc.sync_post("999_001")


class TestIsPostFromCurrentPage:
    def test_post_with_from_field(self):
        from app.services.sync import SyncService
        from app.config import AppConfig

        # Create a dummy config to pass to SyncService init
        cfg = AppConfig(account_id=0, account_name="", page_access_token="",
                        verify_token="", page_id="")
        svc = SyncService(cfg)
        post = {"id": "123_456", "from": {"id": "123"}}
        assert svc._is_post_from_current_page(post, "123")
        assert not svc._is_post_from_current_page(post, "999")

    def test_post_with_id_prefix_matching(self):
        from app.services.sync import SyncService
        from app.config import AppConfig

        cfg = AppConfig(account_id=0, account_name="", page_access_token="",
                        verify_token="", page_id="")
        svc = SyncService(cfg)
        post = {"id": "123_456"}
        assert svc._is_post_from_current_page(post, "123")
        assert not svc._is_post_from_current_page(post, "999")


class TestCountCommentTree:
    def test_count_simple(self):
        from app.services.sync import SyncService
        from app.config import AppConfig

        cfg = AppConfig(account_id=0, account_name="", page_access_token="",
                        verify_token="", page_id="")
        svc = SyncService(cfg)
        assert svc._count_comment_tree({}) == 1

    def test_count_with_replies(self):
        from app.services.sync import SyncService
        from app.config import AppConfig

        cfg = AppConfig(account_id=0, account_name="", page_access_token="",
                        verify_token="", page_id="")
        svc = SyncService(cfg)
        comment = {
            "id": "c1",
            "replies": {"data": [
                {"id": "r1"},
                {"id": "r2", "replies": {"data": [{"id": "rr1"}]}},
            ]},
        }
        assert svc._count_comment_tree(comment) == 4