"""Tests for api.py — REST API endpoints."""
import pytest


# ============================================================================
# Settings endpoints
# ============================================================================
class TestSettingsAPI:
    def test_get_settings(self, auth_client):
        resp = auth_client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "accounts" in data
        assert "model" in data

    def test_create_account(self, auth_client):
        resp = auth_client.post("/api/settings/accounts", json={
            "name": "New Page",
            "page_access_token": "EAA_test",
            "verify_token": "verify123",
            "page_id": "99999",
            "api_version": "v25.0",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_update_model_config(self, auth_client):
        resp = auth_client.put("/api/settings/model", json={
            "reply_api_base_url": "https://api.test.com/v1",
            "reply_api_key": "sk-test123",
            "reply_model": "test-model",
            "video_api_base_url": "",
            "video_api_key": "",
            "video_model": "",
            "prompt_template": "reply_prompt.j2",
        })
        assert resp.status_code == 200

    def test_test_model_connection(self, auth_client, httpx_mock):
        httpx_mock.add_response(
            json={"choices": [{"message": {"content": "hi"}}]},
        )
        resp = auth_client.post("/api/settings/model/test", json={
            "reply_api_base_url": "https://api.test.com/v1",
            "reply_api_key": "sk-test",
            "reply_model": "gpt-4",
        })
        assert resp.status_code == 200
        assert "成功" in resp.json()["message"]

    def test_test_video_model_connection(self, auth_client, httpx_mock):
        httpx_mock.add_response(
            json={"choices": [{"message": {"content": "hi"}}]},
        )
        resp = auth_client.post("/api/settings/model/test-video", json={
            "video_api_base_url": "https://api.video.test/v1",
            "video_api_key": "sk-video-test",
            "video_model": "gpt-4-vision",
        })
        assert resp.status_code == 200

    def test_change_password(self, auth_client):
        resp = auth_client.post("/api/admin/change-password", json={
            "old_password": "TestAdminPassword123!@#$",
            "new_password": "NewStrongPassword123!@#$",
        })
        assert resp.status_code == 200


# ============================================================================
# Page Profile
# ============================================================================
class TestPageProfileAPI:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_page_profile, create_account
        from tests.factories import make_facebook_page_profile, make_account

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))

    def test_get_page_profile(self, auth_client):
        resp = auth_client.get("/api/page-profile")
        assert resp.status_code == 200

    def test_refresh_page_profile(self, auth_client, httpx_mock):
        httpx_mock.add_response(
            json={"id": "123456789", "name": "Refreshed", "fan_count": 5000,
                  "username": "test", "link": "https://fb.com/test",
                  "category": "Community",
                  "picture": {"data": {"url": "https://..."}}},
        )
        resp = auth_client.post("/api/page-profile/refresh")
        assert resp.status_code == 200


# ============================================================================
# Comments
# ============================================================================
class TestCommentsAPI:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_page_profile, create_account, upsert_post
        from tests.factories import make_facebook_page_profile, make_account, make_facebook_post

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))

    def test_get_post_comments(self, auth_client):
        resp = auth_client.get("/api/posts/123456789_001/comments")
        assert resp.status_code == 200

    def test_ai_reply(self, auth_client, httpx_mock):
        from app.repositories import upsert_comment, upsert_model_config
        from tests.factories import make_facebook_comment, make_model_config

        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="api_c1", message="Nice!"))
        upsert_model_config(**make_model_config(
            reply_url="https://api.test.com/v1",
            reply_key="sk-test",
            reply_model="gpt-4",
        ))

        httpx_mock.add_response(
            json={"choices": [{"message": {"content": "Thanks!"}}]},
        )
        resp = auth_client.post("/api/comments/api_c1/ai-reply")
        assert resp.status_code == 200


# ============================================================================
# Monitors API
# ============================================================================
class TestMonitorsAPI:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_page_profile, create_account, upsert_post
        from tests.factories import make_facebook_page_profile, make_account, make_facebook_post

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))

    def test_list_monitors(self, auth_client):
        resp = auth_client.get("/api/monitors")
        assert resp.status_code == 200

    def test_create_monitor(self, auth_client):
        resp = auth_client.post("/api/monitors", json={
            "post_id": "123456789_001",
            "interval_seconds": 1800,
            "max_depth": 1,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"


# ============================================================================
# Sync API
# ============================================================================
class TestSyncAPI:
    def test_sync_status(self, auth_client):
        resp = auth_client.get("/api/sync/status", params={"task": "post_sync"})
        assert resp.status_code == 200

    def test_sync_stop(self, auth_client):
        resp = auth_client.post("/api/sync/stop")
        assert resp.status_code == 200


# ============================================================================
# Auth edge cases
# ============================================================================
class TestAuthEdgeCases:
    def test_unauthenticated_api_access(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 401
        assert "detail" in resp.json()

    def test_unauthenticated_page_access(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303