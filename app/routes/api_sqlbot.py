"""AI SQL bot endpoints for private-message analysis."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import load_config
from app.repositories import get_canonical_page_id
from app.services.sqlbot import ChatSQLBotService

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


class SQLBotQueryPayload(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


@router.post("/sqlbot/query")
async def query_sqlbot(payload: SQLBotQueryPayload):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入要分析的问题")

    try:
        config = load_config()
        page_id = get_canonical_page_id(config.page_id)
        result = await ChatSQLBotService(config, page_id=page_id).answer(question)
        return {
            "question": result.question,
            "answer": result.answer,
            "sql": result.sql,
            "params": result.params,
            "rows": result.rows,
            "columns": result.columns,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "plan_note": result.plan_note,
        }
    except HTTPException:
        raise
    except RuntimeError as exc:
        logger.warning("[sqlbot] query failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[sqlbot] unexpected failure")
        raise HTTPException(status_code=500, detail=f"AI 数据分析失败: {exc}") from exc
