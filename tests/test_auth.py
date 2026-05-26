"""Tests for auth.py — authentication, sessions, IP rate limiting."""
import pytest


class TestAuthenticateAdmin:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import update_admin_password

        update_admin_password(password_hash="testhash", password_salt="a" * 32,
                              password_iterations=390_000)

    def test_authenticate_success(self, setup_db):
        from app.auth import authenticate_admin
        from app.security import hash_password

        # Update admin with a real hash for a known password
        from app.repositories import update_admin_password
        from app.security import generate_salt

        salt = generate_salt()
        pw_hash = hash_password("correct-password", salt)
        update_admin_password(password_hash=pw_hash, password_salt=salt,
                              password_iterations=390_000)

        # Mock request
        class MockReq:
            headers = {"x-forwarded-for": "10.0.0.1"}
            client = type("c", (), {"host": "10.0.0.1"})()

        ok, msg = authenticate_admin("correct-password", MockReq())
        assert ok is True
        assert msg == "ok"

    def test_authenticate_wrong_password(self, setup_db):
        from app.auth import authenticate_admin
        from app.security import hash_password, generate_salt
        from app.repositories import update_admin_password

        salt = generate_salt()
        pw_hash = hash_password("right-password", salt)
        update_admin_password(password_hash=pw_hash, password_salt=salt,
                              password_iterations=390_000)

        class MockReq:
            headers = {}
            client = type("c", (), {"host": "10.0.0.2"})()

        ok, msg = authenticate_admin("wrong-password", MockReq())
        assert ok is False
        assert "密码" in msg or "错误" in msg

    def test_register_failed_login_tracks_ip(self, setup_db):
        from app.repositories import register_failed_login

        # Should not raise
        count = register_failed_login("10.0.0.3")
        assert count == 1

    def test_ip_lock_after_multiple_failures(self, setup_db):
        from app.repositories import register_failed_login, is_ip_locked

        for _ in range(5):
            register_failed_login("10.0.0.4")
        assert is_ip_locked("10.0.0.4")

    def test_clear_login_attempts(self, setup_db):
        from app.repositories import register_failed_login, clear_login_attempts, is_ip_locked

        register_failed_login("10.0.0.5")
        clear_login_attempts("10.0.0.5")
        assert not is_ip_locked("10.0.0.5")


class TestSessions:
    def test_create_and_get_session(self, setup_db):
        from app.repositories import create_admin_session, get_admin_session

        create_admin_session(session_id="testsess1", ip="1.2.3.4",
                             user_agent="TestUA")
        sess = get_admin_session("testsess1")
        assert sess is not None
        assert sess["ip"] == "1.2.3.4"

    def test_is_authenticated_true(self, setup_db):
        from app.auth import is_authenticated
        from app.repositories import create_admin_session

        create_admin_session(session_id="validsess", ip="1.2.3.4",
                             user_agent="TestUA")
        assert is_authenticated("validsess") is True

    def test_is_authenticated_false_none(self, setup_db):
        from app.auth import is_authenticated

        assert is_authenticated(None) is False
        assert is_authenticated("") is False

    def test_is_authenticated_false_expired(self, setup_db):
        from app.auth import is_authenticated
        from app.repositories import create_admin_session
        from app.database import get_connection

        create_admin_session(session_id="expiredsess", ip="1.2.3.4",
                             user_agent="TestUA")
        # Force-expire
        with get_connection() as conn:
            conn.execute(
                "UPDATE admin_sessions SET expires_at = '2020-01-01 00:00:00' WHERE session_id = ?",
                ("expiredsess",))
        assert is_authenticated("expiredsess") is False


class TestClientIP:
    def test_get_client_ip_from_xff(self):
        from app.auth import get_client_ip

        class MockReq:
            headers = {"x-forwarded-for": "192.168.1.100, 10.0.0.1"}
            client = type("c", (), {"host": "10.0.0.1"})()

        assert get_client_ip(MockReq()) == "192.168.1.100"

    def test_get_client_ip_fallback(self):
        from app.auth import get_client_ip

        class MockReq:
            headers = {}
            client = type("c", (), {"host": "10.0.0.99"})()

        assert get_client_ip(MockReq()) == "10.0.0.99"