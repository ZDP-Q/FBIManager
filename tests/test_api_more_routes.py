"""Additional tests for api.py routes — prompts, auto-monitor, accounts, webhook."""
import pytest


# ============================================================================
# Prompts API
# ============================================================================
class TestPromptsAPI:
    def test_list_prompts(self, auth_client, setup_db):
        from app.repositories import create_account, upsert_page_profile
        from tests.factories import make_account, make_facebook_page_profile
        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        resp = auth_client.get("/api/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_activate_prompt(self, auth_client, setup_db):
        from app.repositories import create_account, upsert_page_profile
        from tests.factories import make_account, make_facebook_page_profile
        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        resp = auth_client.post("/api/prompts/activate", json={
            "filename": "Elio.j2",
        })
        assert resp.status_code == 200


# ============================================================================
# Posts API
# ============================================================================
class TestPostsAPI:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_page_profile, create_account, upsert_post
        from tests.factories import make_facebook_page_profile, make_account, make_facebook_post

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        upsert_post("123456789", make_facebook_post(post_id="123456789_001",
                                                      message="Test"))
        upsert_post("123456789", make_facebook_post(post_id="123456789_002",
                                                      message="Test 2"))

    def test_list_posts(self, auth_client):
        resp = auth_client.get("/api/posts", params={"limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_delete_posts(self, auth_client):
        resp = auth_client.post("/api/posts/delete", json={
            "post_ids": ["123456789_002"],
        })
        assert resp.status_code == 200

    def test_clear_all_posts(self, auth_client):
        resp = auth_client.post("/api/posts/clear-all")
        assert resp.status_code == 200


# ============================================================================
# Auto-Monitor API
# ============================================================================
class TestAutoMonitorAPI:
    def test_get_auto_monitor_settings(self, auth_client):
        resp = auth_client.get("/api/auto-monitor/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "schedules" in data

    def test_update_auto_monitor_config(self, auth_client):
        resp = auth_client.patch("/api/auto-monitor/config", json={
            "enabled": True,
            "max_posts": 5,
        })
        assert resp.status_code == 200

    def test_add_schedule(self, auth_client):
        resp = auth_client.post("/api/auto-monitor/schedules", json={
            "trigger_time": "14:30",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"

    def test_add_invalid_schedule_time(self, auth_client):
        resp = auth_client.post("/api/auto-monitor/schedules", json={
            "trigger_time": "25:00",
        })
        assert resp.status_code == 400


# ============================================================================
# Account Management API
# ============================================================================
class TestAccountManagementAPI:
    def test_delete_account(self, auth_client):
        from app.repositories import create_account
        from tests.factories import make_account

        create_account(**make_account(page_id="del_me", token="t", is_active=0))
        # Get the account ID from settings
        r = auth_client.get("/api/settings")
        accounts = r.json()["accounts"]
        # Find the one we just created
        acc = next((a for a in accounts if a["page_id"] == "del_me"), None)
        if acc:
            resp = auth_client.delete(f"/api/settings/accounts/{acc['id']}")
            assert resp.status_code == 200

    def test_activate_account(self, auth_client):
        from app.repositories import create_account
        from tests.factories import make_account

        create_account(**make_account(page_id="activate_me", token="t", is_active=0))
        r = auth_client.get("/api/settings")
        accounts = r.json()["accounts"]
        acc = next((a for a in accounts if a["page_id"] == "activate_me"), None)
        if acc:
            resp = auth_client.post(f"/api/settings/accounts/{acc['id']}/activate")
            assert resp.status_code == 200

    def test_export_accounts(self, auth_client):
        resp = auth_client.get("/api/settings/accounts/export")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_import_accounts(self, auth_client):
        from tests.factories import make_account

        import_data = [make_account(
            name="Imported", page_id="imported_1", token="tok",
            verify="v", is_active=0,
        )]
        resp = auth_client.post("/api/settings/accounts/import", json=import_data)
        assert resp.status_code == 200


# ============================================================================
# Webhook routes
# ============================================================================
class TestWebhookRoutes:
    def test_verify_webhook_valid(self, client, setup_db):
        from app.repositories import create_account
        from tests.factories import make_account

        create_account(**make_account(page_id="webhook_page", token="t",
                                       verify="my_token", is_active=1))
        resp = client.get("/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "my_token",
            "hub.challenge": "challenge_123",
        })
        assert resp.status_code == 200

    def test_verify_webhook_invalid_token(self, client):
        resp = client.get("/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "test",
        })
        assert resp.status_code == 403

    def test_verify_webhook_wrong_mode(self, client):
        resp = client.get("/webhook", params={
            "hub.mode": "unsubscribe",
        })
        assert resp.status_code == 403

    def test_handle_webhook_post(self, client):
        resp = client.post("/webhook", json={
            "object": "page",
            "entry": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"


# ============================================================================
# Replied Comments API
# ============================================================================
class TestRepliedAPI:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import (
            upsert_page_profile, create_account, upsert_post,
            create_monitor, upsert_comment, mark_replied,
        )
        from tests.factories import (
            make_facebook_page_profile, make_account, make_facebook_post,
            make_facebook_comment,
        )

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))
        self.mid = create_monitor("123456789_001")
        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="rpc1", message="Hi"))
        mark_replied("rpc1", "123456789_001", self.mid, "Thanks!")

    def test_get_replied_for_monitor(self, auth_client):
        resp = auth_client.get(f"/api/monitors/{self.mid}/replied")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["comment_id"] == "rpc1"

    def test_get_replied_limit(self, auth_client):
        resp = auth_client.get(f"/api/monitors/{self.mid}/replied", params={"limit": 1})
        assert resp.status_code == 200