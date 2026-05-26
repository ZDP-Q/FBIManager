"""Tests for security.py — password hashing, session IDs, time helpers."""
import re
from datetime import datetime, timedelta, UTC

import pytest


class TestPasswordHashing:
    def test_hash_password_is_deterministic(self):
        from app.security import hash_password

        h1 = hash_password("mypassword", "a" * 32, 100_000)
        h2 = hash_password("mypassword", "a" * 32, 100_000)
        assert h1 == h2

    def test_hash_password_different_salts_produce_different_hashes(self):
        from app.security import hash_password

        h1 = hash_password("mypassword", "a" * 32, 100_000)
        h2 = hash_password("mypassword", "b" * 32, 100_000)
        assert h1 != h2

    def test_hash_password_different_passwords_produce_different_hashes(self):
        from app.security import hash_password

        h1 = hash_password("password1", "a" * 32, 100_000)
        h2 = hash_password("password2", "a" * 32, 100_000)
        assert h1 != h2

    def test_verify_password_correct(self):
        from app.security import generate_salt, hash_password, verify_password

        salt = generate_salt()
        pw_hash = hash_password("correct-password", salt)
        assert verify_password("correct-password", salt_hex=salt,
                               expected_hash_hex=pw_hash, iterations=390_000)

    def test_verify_password_incorrect(self):
        from app.security import generate_salt, hash_password, verify_password

        salt = generate_salt()
        pw_hash = hash_password("correct-password", salt)
        assert not verify_password("wrong-password", salt_hex=salt,
                                   expected_hash_hex=pw_hash, iterations=390_000)

    def test_generate_salt_length(self):
        from app.security import generate_salt

        salt = generate_salt()
        assert len(salt) == 32  # 16 bytes = 32 hex chars
        assert all(c in "0123456789abcdef" for c in salt)


class TestSession:
    def test_generate_session_id_length(self):
        from app.security import generate_session_id

        sid = generate_session_id()
        # 48 random bytes = 64 URL-safe base64 chars
        assert len(sid) == 64

    def test_generate_session_id_unique(self):
        from app.security import generate_session_id

        ids = {generate_session_id() for _ in range(100)}
        assert len(ids) == 100


class TestStrongPassword:
    def test_generate_meets_criteria(self):
        from app.security import generate_strong_password

        for _ in range(20):
            pw = generate_strong_password()
            assert len(pw) >= 16
            assert any(c.islower() for c in pw)
            assert any(c.isupper() for c in pw)
            assert any(c.isdigit() for c in pw)
            assert any(c in "!@#$%^&*()-_=+[]{}" for c in pw)

    def test_generate_rejects_short_length(self):
        from app.security import generate_strong_password

        with pytest.raises(ValueError, match="16"):
            generate_strong_password(length=10)

    def test_is_strong_password_valid(self):
        from app.security import is_strong_password

        assert is_strong_password("a" * 14 + "A1")  # 16 chars, has letter + digit
        assert is_strong_password("TestAdminPassword123!@#$")

    def test_is_strong_password_too_short(self):
        from app.security import is_strong_password

        assert not is_strong_password("Ab1")

    def test_is_strong_password_no_digit(self):
        from app.security import is_strong_password

        assert not is_strong_password("Abcdefghijklmnop")  # 16 chars, letters only

    def test_is_strong_password_no_letter(self):
        from app.security import is_strong_password

        assert not is_strong_password("1234567890123456")  # 16 chars, digits only


class TestTimeHelpers:
    def test_now_utc_returns_utc(self):
        from app.security import now_utc

        dt = now_utc()
        assert dt.tzinfo == UTC

    def test_now_utc_sql_format(self):
        from app.security import now_utc_sql

        result = now_utc_sql()
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", result)

    def test_to_sql_converts_to_utc(self):
        from app.security import to_sql
        from datetime import timezone, timedelta

        # Create a datetime in UTC+8
        tz_8 = timezone(timedelta(hours=8))
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=tz_8)
        result = to_sql(dt)
        # Should be converted to 04:00 UTC
        assert result.startswith("2025-06-01 04:00:00")

    def test_session_expiry_sql_is_future(self):
        from app.security import now_utc, session_expiry_sql

        expiry = session_expiry_sql(hours=1)
        now = now_utc()
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        delta = expiry_dt - now
        assert timedelta(minutes=55) < delta < timedelta(minutes=65)