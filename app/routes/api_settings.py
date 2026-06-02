"""Settings, account management, model config, and admin password endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_config
from app.repositories import (
    create_account, delete_account, get_account_by_id, get_active_account,
    get_model_config, get_page_profile, list_accounts, set_active_account, update_account,
    upsert_model_config, get_admin_auth, update_admin_password, delete_all_admin_sessions,
)
from app.services.ai_reply import AIReplyService
from app.services.facebook import FacebookService
from app.security import PBKDF2_ITERATIONS, generate_salt, hash_password, is_strong_password, verify_password

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


# --- Page profile ---

@router.get("/page-profile")
async def page_profile():
    config = load_config()
    profile = get_page_profile(page_id=config.page_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="尚未同步到主页信息")
    return profile


@router.post("/page-profile/refresh")
async def refresh_page_profile():
    config = load_config()
    async with FacebookService(config) as facebook:
        try:
            profile = await facebook.fetch_page_profile()
            from app.repositories import upsert_page_profile
            upsert_page_profile(profile)
            return profile
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"刷新主页信息失败: {exc}") from exc


class AccountPayload(BaseModel):
    name: str = ""
    page_access_token: str
    verify_token: str
    page_id: str
    api_version: str = "v25.0"
    app_secret: str = ""


class ModelConfigPayload(BaseModel):
    reply_api_base_url: str = ""
    reply_api_key: str = ""
    reply_model: str = ""
    video_api_base_url: str = ""
    video_api_key: str = ""
    video_model: str = ""
    prompt_template: str = "reply_prompt.j2"


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


@router.get("/settings")
async def get_settings():
    accounts = list_accounts()
    active = get_active_account()
    model = get_model_config() or {
        "reply_api_base_url": "", "reply_api_key": "", "reply_model": "",
        "video_api_base_url": "", "video_api_key": "", "video_model": "",
        "prompt_template": "reply_prompt.j2",
    }
    return {"accounts": accounts, "active_account_id": active["id"] if active else None, "model": model}


@router.post("/settings/accounts")
async def create_account_api(payload: AccountPayload):
    page_id = payload.page_id.strip()
    token = payload.page_access_token.strip()
    verify = payload.verify_token.strip()
    if not page_id or not token or not verify:
        raise HTTPException(status_code=400, detail="PAGE_ID、PAGE_ACCESS_TOKEN、VERIFY_TOKEN 不能为空")
    try:
        account_id = create_account(
            name=payload.name.strip() or f"账号 {page_id}",
            page_access_token=token, verify_token=verify,
            page_id=page_id, api_version=(payload.api_version.strip() or "v25.0"),
            app_secret=payload.app_secret.strip(), is_active=0,
        )
        return {"status": "success", "account_id": account_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"保存账号失败: {exc}") from exc


@router.put("/settings/accounts/{account_id}")
async def update_account_api(account_id: int, payload: AccountPayload):
    account = get_account_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    page_id = payload.page_id.strip()
    token = payload.page_access_token.strip()
    verify = payload.verify_token.strip()
    if not page_id or not token or not verify:
        raise HTTPException(status_code=400, detail="PAGE_ID、PAGE_ACCESS_TOKEN、VERIFY_TOKEN 不能为空")
    try:
        update_account(
            account_id, name=payload.name.strip() or f"账号 {page_id}",
            page_access_token=token, verify_token=verify,
            page_id=page_id, api_version=(payload.api_version.strip() or "v25.0"),
            app_secret=payload.app_secret.strip(),
        )
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"更新账号失败: {exc}") from exc


@router.post("/settings/accounts/{account_id}/activate")
async def activate_account_api(account_id: int):
    account = get_account_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    set_active_account(account_id)
    return {"status": "success"}


@router.delete("/settings/accounts/{account_id}")
async def delete_account_api(account_id: int):
    account = get_account_by_id(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    delete_account(account_id)
    return {"status": "success"}


@router.get("/settings/accounts/export")
async def export_accounts_api():
    accounts = list_accounts()
    return [{
        "name": acc["name"], "page_id": acc["page_id"],
        "page_access_token": acc["page_access_token"], "verify_token": acc["verify_token"],
        "api_version": acc["api_version"],
    } for acc in accounts]


@router.post("/settings/accounts/import")
async def import_accounts_api(payload: list[dict]):
    try:
        from app.repositories import bulk_import_accounts
        count = bulk_import_accounts(payload)
        return {"status": "success", "count": count}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"导入失败: {exc}")


@router.put("/settings/model")
async def update_model_api(payload: ModelConfigPayload):
    upsert_model_config(
        reply_api_base_url=payload.reply_api_base_url.strip(),
        reply_api_key=payload.reply_api_key.strip(),
        reply_model=payload.reply_model.strip(),
        video_api_base_url=payload.video_api_base_url.strip(),
        video_api_key=payload.video_api_key.strip(),
        video_model=payload.video_model.strip(),
        prompt_template=payload.prompt_template.strip() or "reply_prompt.j2",
    )
    return {"status": "success"}


def _make_temp_config(payload: ModelConfigPayload):
    from app.config import AppConfig
    return AppConfig(
        account_id=0, account_name="", page_access_token="", verify_token="", page_id="",
        reply_api_base_url=payload.reply_api_base_url.strip(),
        reply_api_key=payload.reply_api_key.strip(),
        reply_model=payload.reply_model.strip(),
        video_api_base_url=payload.video_api_base_url.strip(),
        video_api_key=payload.video_api_key.strip(),
        video_model=payload.video_model.strip(),
        prompt_template=payload.prompt_template.strip() or "reply_prompt.j2",
    )


@router.post("/settings/model/test")
async def test_model_api(payload: ModelConfigPayload):
    ai_service = AIReplyService(_make_temp_config(payload))
    try:
        result = await ai_service.test_reply_connection()
        return {"status": "success", "message": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/model/test-video")
async def test_video_model_api(payload: ModelConfigPayload):
    ai_service = AIReplyService(_make_temp_config(payload))
    try:
        result = await ai_service.test_video_connection()
        return {"status": "success", "message": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/change-password")
async def change_admin_password(payload: ChangePasswordPayload):
    auth = get_admin_auth()
    if auth is None:
        raise HTTPException(status_code=500, detail="管理员账号不存在")
    old_ok = verify_password(
        payload.old_password,
        salt_hex=str(auth.get("password_salt", "")),
        expected_hash_hex=str(auth.get("password_hash", "")),
        iterations=int(auth.get("password_iterations", PBKDF2_ITERATIONS)),
    )
    if not old_ok:
        raise HTTPException(status_code=400, detail="旧密码错误")
    if not is_strong_password(payload.new_password):
        raise HTTPException(status_code=400, detail="新密码不符合强密码要求（至少16位，包含字母和数字）")
    salt = generate_salt()
    pwd_hash = hash_password(payload.new_password, salt, PBKDF2_ITERATIONS)
    update_admin_password(password_hash=pwd_hash, password_salt=salt, password_iterations=PBKDF2_ITERATIONS)
    delete_all_admin_sessions()
    return {"status": "success", "message": "密码已更新，请重新登录"}
