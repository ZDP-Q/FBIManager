from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.json"


@dataclass(slots=True)
class AppConfig:
    account_id: int
    account_name: str
    page_access_token: str
    verify_token: str
    page_id: str
    api_version: str = "v25.0"
    ai_api_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    prompt_template: str = "reply_prompt.j2"

    @property
    def graph_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_base_url and self.ai_api_key and self.ai_model)


def read_legacy_json_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_config(*, account_id: int | None = None, page_id: str | None = None) -> AppConfig:
    from app.repositories import (
        get_account_by_id,
        get_account_by_page_id,
        get_active_account,
        get_model_config,
    )

    account: dict | None
    if account_id is not None:
        account = get_account_by_id(account_id)
    elif page_id:
        account = get_account_by_page_id(page_id)
    else:
        account = get_active_account()

    if account is None:
        raise RuntimeError("未找到可用账号配置，请先在 Web 页面保存账号配置")

    model = get_model_config() or {}

    return AppConfig(
        account_id=int(account["id"]),
        account_name=str(account.get("name", "") or "未命名账号"),
        page_access_token=str(account.get("page_access_token", "")),
        verify_token=str(account.get("verify_token", "")),
        page_id=str(account.get("page_id", "")),
        api_version=str(account.get("api_version", "v25.0") or "v25.0"),
        ai_api_base_url=str(model.get("ai_api_base_url", "")),
        ai_api_key=str(model.get("ai_api_key", "")),
        ai_model=str(model.get("ai_model", "")),
        prompt_template=str(model.get("prompt_template", "reply_prompt.j2") or "reply_prompt.j2"),
    )
