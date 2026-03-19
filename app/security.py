from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timedelta, UTC

PBKDF2_ITERATIONS = 390_000
SESSION_TTL_HOURS = 8


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_utc_sql() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M:%S")


def to_sql(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def session_expiry_sql(hours: int = SESSION_TTL_HOURS) -> str:
    return to_sql(now_utc() + timedelta(hours=hours))


def generate_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt_hex: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        iterations,
    )
    return digest.hex()


def verify_password(password: str, *, salt_hex: str, expected_hash_hex: str, iterations: int) -> bool:
    actual = hash_password(password, salt_hex, iterations)
    return secrets.compare_digest(actual, expected_hash_hex)


def generate_session_id() -> str:
    return secrets.token_urlsafe(48)


def generate_strong_password(length: int = 24) -> str:
    if length < 16:
        raise ValueError("密码长度至少为 16")

    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
            and any(c in "!@#$%^&*()-_=+[]{}" for c in candidate)
        ):
            return candidate


def is_strong_password(password: str) -> bool:
    if len(password) < 16:
        return False
    has_alpha = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    return has_alpha and has_digit
