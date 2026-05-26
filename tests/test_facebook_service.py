"""Tests for FacebookService — Graph API calls with httpx mocking."""
import pytest


@pytest.fixture
def fb_config(setup_db):
    from app.config import AppConfig
    from app.repositories import create_account, upsert_model_config
    from tests.factories import make_account, make_model_config

    create_account(**make_account(page_id="123456789", token="EAA_test_token",
                                   verify="verify_me", is_active=1))
    upsert_model_config(**make_model_config())

    return AppConfig(
        account_id=1, account_name="Test",
        page_access_token="EAA_test_token",
        verify_token="verify_me", page_id="123456789",
    )


@pytest.fixture
def fb(fb_config):
    from app.services.facebook import FacebookService

    return FacebookService(fb_config)


class TestFacebookService:
    @pytest.mark.asyncio
    async def test_fetch_page_profile(self, fb, httpx_mock):
        httpx_mock.add_response(
            json={"id": "123456789", "name": "Test Page", "fan_count": 1000},
        )
        profile = await fb.fetch_page_profile()
        assert profile["name"] == "Test Page"

    @pytest.mark.asyncio
    async def test_fetch_posts(self, fb, httpx_mock):
        httpx_mock.add_response(
            json={"data": [
                {"id": "123456789_001", "message": "Post 1",
                 "created_time": "2025-01-01T00:00:00+0000"},
            ]},
        )
        result = await fb.fetch_posts(limit=10)
        assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_fetch_posts_fallback_to_posts_edge(self, fb, httpx_mock):
        httpx_mock.add_response(status_code=400)
        httpx_mock.add_response(
            json={"data": [
                {"id": "123456789_002", "message": "Post 2",
                 "created_time": "2025-01-02T00:00:00+0000"},
            ]},
        )
        result = await fb.fetch_posts(limit=10)
        assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_fetch_comments_for_post(self, fb, httpx_mock):
        # Main comments request
        httpx_mock.add_response(
            json={
                "data": [
                    {"id": "c1", "message": "Nice!",
                     "from": {"id": "u1", "name": "User1"},
                     "created_time": "2025-01-01T00:00:00+0000"},
                ],
                "paging": {"cursors": {"before": "Q1", "after": "Q2"}},
            },
        )
        # Replies request (there's 1 comment, so 1 reply fetch)
        httpx_mock.add_response(json={"data": []})
        comments, cursors = await fb.fetch_comments_for_post("test_post")
        assert len(comments) == 1

    @pytest.mark.asyncio
    async def test_send_reply(self, fb, httpx_mock):
        httpx_mock.add_response(json={"id": "reply1"})
        result = await fb.send_reply("c1", "Thanks!")
        assert result["id"] == "reply1"

    @pytest.mark.asyncio
    async def test_delete_comment(self, fb, httpx_mock):
        httpx_mock.add_response(json={"success": True})
        result = await fb.delete_comment("c2")
        assert result is True

    def test_extract_attachment_info_photo(self, fb):
        comment = {
            "attachment": {
                "type": "photo",
                "media": {"image": {"src": "https://fb.com/photo.jpg"}},
            },
        }
        result = fb.extract_attachment_info(comment)
        assert result is not None
        _, url = result
        assert "https://fb.com/photo.jpg" in url

    def test_extract_attachment_info_sticker(self, fb):
        comment = {"story": "User sent a sticker", "message": ""}
        result = fb.extract_attachment_info(comment)
        assert result is not None

    def test_extract_attachment_info_none(self, fb):
        comment = {"message": "Just text"}
        result = fb.extract_attachment_info(comment)
        assert result is None

    @pytest.mark.asyncio
    async def test_download_attachment_bytes(self, fb, httpx_mock):
        httpx_mock.add_response(content=b"fake-image-data")
        result = await fb.download_attachment_bytes("https://example.com/img.jpg")
        assert result == b"fake-image-data"

    @pytest.mark.asyncio
    async def test_request_retry_on_5xx(self, fb, httpx_mock):
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(
            json={"id": "123456789", "name": "After Retry"},
        )
        profile = await fb.fetch_page_profile()
        assert profile["name"] == "After Retry"