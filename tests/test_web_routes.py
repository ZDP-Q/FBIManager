"""Tests for web.py — HTML page rendering routes."""
import pytest


class TestLoginPage:
    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_login_submit_success(self, client):
        resp = client.post("/login", data={
            "password": "TestAdminPassword123!@#$",
            "next": "/",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    def test_login_submit_wrong_password(self, client):
        resp = client.post("/login", data={
            "password": "wrong-password-12345",
            "next": "/",
        })
        assert resp.status_code == 401

    def test_logout(self, auth_client):
        resp = auth_client.post("/logout", follow_redirects=False)
        assert resp.status_code == 303


class TestPagesAuthenticated:
    """Pages that don't need active account data."""

    def test_home_page(self, auth_client):
        resp = auth_client.get("/")
        assert resp.status_code == 200

    def test_personas_page(self, auth_client):
        resp = auth_client.get("/personas")
        assert resp.status_code == 200

    def test_chats_page(self, auth_client):
        resp = auth_client.get("/chats")
        assert resp.status_code == 200

    def test_chat_analytics_page(self, auth_client):
        resp = auth_client.get("/chat-analytics")
        assert resp.status_code == 200


class TestPagesWithAccount:
    """Pages that need a page profile and active account."""

    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_page_profile, create_account, upsert_post
        from tests.factories import make_facebook_page_profile, make_account, make_facebook_post

        upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
        create_account(**make_account(page_id="123456789", is_active=1))
        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))

    def test_comments_page(self, auth_client):
        resp = auth_client.get("/comments")
        assert resp.status_code == 200

    def test_monitors_page(self, auth_client):
        resp = auth_client.get("/monitors")
        assert resp.status_code == 200

    def test_schedule_page(self, auth_client):
        resp = auth_client.get("/schedule")
        assert resp.status_code == 200
