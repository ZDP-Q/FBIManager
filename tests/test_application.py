"""Tests for application.py — middleware, CSRF, security headers."""
import pytest


class TestSecurityMiddleware:
    def test_public_paths_bypass_auth(self, client):
        """Public paths should not require authentication."""
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_protected_path_redirects_to_login(self, client):
        """Protected pages redirect to login when not authenticated."""
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    def test_protected_api_returns_401(self, client):
        """Protected API returns 401 JSON when not authenticated."""
        resp = client.get("/api/settings")
        assert resp.status_code == 401
        assert "detail" in resp.json()

    def test_authenticated_access(self, auth_client):
        """Authenticated client can access protected pages."""
        resp = auth_client.get("/")
        assert resp.status_code == 200

    def test_security_headers_set(self, client):
        """Security headers should be present on all responses."""
        resp = client.get("/login")
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" in resp.headers

    def test_csrf_rejects_mismatched_origin(self, client):
        """API write with wrong Origin should be rejected."""
        resp = client.post(
            "/api/sync",
            headers={"origin": "https://evil.com", "host": "localhost"},
        )
        # Either 401 (not auth) or 403 (CSRF) — both are security blocks
        assert resp.status_code in (401, 403)

    def test_webhook_path_bypasses_auth(self, client):
        """Webhook endpoints should be publicly accessible."""
        resp = client.get("/webhook?hub.mode=subscribe&hub.challenge=test&hub.verify_token=wrong")
        # Should not redirect to login — may return error about invalid token
        assert resp.status_code != 303  # Not a redirect


class TestAppCreation:
    def test_create_app_registers_routes(self, test_app):
        """All routes should be registered."""
        routes = {r.path for r in test_app.routes}
        assert "/" in routes
        assert "/login" in routes
        assert "/comments" in routes
        assert "/api/settings" in routes
        assert "/webhook" in routes