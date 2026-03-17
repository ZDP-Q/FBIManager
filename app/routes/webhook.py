from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import load_config
from app.repositories import get_account_by_verify_token
from app.services.webhook import WebhookService

router = APIRouter()


@router.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")

    account = get_account_by_verify_token(token or "")
    if mode == "subscribe" and account is not None:
        return PlainTextResponse(challenge, status_code=200)

    return PlainTextResponse("验证口令错误", status_code=403)


@router.post("/webhook")
async def handle_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "success", "summary": {"processed": 0, "replied": 0, "skipped": 0}},
            status_code=200,
        )

    total = {"processed": 0, "replied": 0, "skipped": 0}
    try:
        entries = payload.get("entry", []) if isinstance(payload, dict) else []
        for entry in entries:
            page_id = str(entry.get("id", ""))
            if not page_id:
                continue
            try:
                config = load_config(page_id=page_id)
            except Exception:
                continue

            partial = await WebhookService(config).process_payload({"object": "page", "entry": [entry]})
            total["processed"] += int(partial.get("processed", 0))
            total["replied"] += int(partial.get("replied", 0))
            total["skipped"] += int(partial.get("skipped", 0))
    except Exception:
        total = {"processed": 0, "replied": 0, "skipped": 0}

    # 无论内部处理结果如何，都返回 200，避免 Facebook 重试风暴
    return JSONResponse({"status": "success", "summary": total}, status_code=200)
