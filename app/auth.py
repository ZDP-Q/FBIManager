from __future__ import annotations

from fastapi import Request

from app.repositories import (
    clear_login_attempts,
    create_admin_session,
    get_admin_auth,
    get_admin_session,
    is_ip_locked,
    register_failed_login,
    touch_admin_session,
)
from app.security import verify_password

SESSION_COOKIE = "fbm_session"


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:120]
    client = request.client.host if request.client else "unknown"
    return str(client)[:120]


def authenticate_admin(password: str, request: Request) -> tuple[bool, str]:
    ip = get_client_ip(request)
    if is_ip_locked(ip):
        return False, "登录失败次数过多，请 15 分钟后再试"

    auth = get_admin_auth()
    if not auth:
        return False, "管理员账户未初始化"

    ok = verify_password(
        password,
        salt_hex=str(auth.get("password_salt", "")),
        expected_hash_hex=str(auth.get("password_hash", "")),
        iterations=int(auth.get("password_iterations", 390000) or 390000),
    )
    if not ok:
        register_failed_login(ip)
        return False, "用户名或密码错误"

    clear_login_attempts(ip)
    return True, "ok"


def create_session(session_id: str, request: Request) -> None:
    ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")
    create_admin_session(session_id=session_id, ip=ip, user_agent=user_agent)


def is_authenticated(session_id: str | None) -> bool:
    if not session_id:
        return False
    session = get_admin_session(session_id)
    if not session:
        return False
    touch_admin_session(session_id)
    return True
