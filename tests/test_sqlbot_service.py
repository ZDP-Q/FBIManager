"""Tests for AI SQL bot service."""
from __future__ import annotations

import json
from datetime import datetime

import pytest


@pytest.fixture
def sqlbot_seed(setup_db):
    from app.repositories import (
        create_account,
        upsert_conversation_message,
        upsert_model_config,
        upsert_page_conversation,
        upsert_page_profile,
    )
    from tests.factories import make_account, make_facebook_page_profile, make_model_config

    upsert_page_profile(make_facebook_page_profile(page_id="123456789"))
    create_account(**make_account(page_id="123456789", token="tok", verify="v", is_active=1))
    upsert_model_config(**make_model_config(
        reply_url="https://api.test.com/v1",
        reply_key="sk-test",
        reply_model="gpt-4",
    ))

    upsert_page_conversation("conv_1", "123456789", "2026-07-02T01:00:00+0000", 0, json.dumps({"data": []}))
    upsert_conversation_message(
        "msg_1", "conv_1", "我想充值会员，但是支付失败了", "user_1", "Alice",
        "2026-07-01T16:30:00+0000",
    )
    upsert_conversation_message(
        "msg_2", "conv_1", "请发一下支付截图", "123456789", "Test Page",
        "2026-07-01T16:31:00+0000",
    )

    upsert_page_profile(make_facebook_page_profile(page_id="other_page"))
    upsert_page_conversation("conv_other", "other_page", "2026-07-02T01:00:00+0000", 0, json.dumps({"data": []}))
    upsert_conversation_message(
        "msg_other", "conv_other", "充值会员支付", "user_2", "Bob",
        "2026-07-01T16:40:00+0000",
    )


@pytest.fixture
def sqlbot(sqlbot_seed):
    from app.config import load_config
    from app.services.sqlbot import ChatSQLBotService

    return ChatSQLBotService(load_config(), page_id="123456789")


class TestChatSQLBotService:
    def test_execute_sql_uses_scoped_views(self, sqlbot):
        rows, columns, truncated = sqlbot._execute_sql(
            """
            SELECT id, message_text, created_at_local
            FROM chat_messages
            WHERE message_text LIKE :kw
            ORDER BY created_at_local ASC
            """,
            {"kw": "%充值%"},
        )

        assert not truncated
        assert columns == ["id", "message_text", "created_at_local"]
        assert [row["id"] for row in rows] == ["msg_1"]
        assert rows[0]["created_at_local"] == "2026-07-02 00:30:00"

    def test_rejects_base_table_access(self, sqlbot):
        with pytest.raises(RuntimeError, match="不能访问原始表"):
            sqlbot._execute_sql("SELECT * FROM conversation_messages", {})

    def test_rejects_write_sql(self, sqlbot):
        with pytest.raises(RuntimeError, match="只允许"):
            sqlbot._execute_sql("DELETE FROM chat_messages", {})

    @pytest.mark.asyncio
    async def test_answer_generates_sql_executes_and_reports(self, sqlbot, httpx_mock):
        from app.services.sqlbot import LOCAL_TZ

        httpx_mock.add_response(
            url="https://api.test.com/v1/chat/completions",
            json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": (
                                "SELECT id, sender_name, message_text, created_at_local "
                                "FROM chat_messages "
                                "WHERE is_page_message = 0 "
                                "AND created_at_local >= :start "
                                "AND created_at_local <= :end "
                                "AND (message_text LIKE :kw0 OR message_text LIKE :kw1 OR message_text LIKE :kw2) "
                                "ORDER BY created_at_local DESC LIMIT 50"
                            ),
                            "params": {
                                "start": "2026-07-01 00:00:00",
                                "end": "2026-07-02 12:00:00",
                                "kw0": "%充值%",
                                "kw1": "%会员%",
                                "kw2": "%支付%",
                            },
                            "note": "查询昨天到当前时间的用户私信关键词。",
                        }, ensure_ascii=False)
                    }
                }]
            },
        )
        httpx_mock.add_response(
            url="https://api.test.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "结论摘要\n命中 1 条充值会员支付相关私信。"}}]},
        )

        result = await sqlbot.answer(
            "拉一下从昨天到今天，查一下关于充值会员支付的话题",
            now=datetime(2026, 7, 2, 12, 0, 0, tzinfo=LOCAL_TZ),
        )

        assert result.row_count == 1
        assert result.rows[0]["id"] == "msg_1"
        assert "命中 1 条" in result.answer


class TestSQLBotAPI:
    def test_query_endpoint(self, auth_client, sqlbot_seed, httpx_mock):
        httpx_mock.add_response(
            url="https://api.test.com/v1/chat/completions",
            json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "sql": (
                                "SELECT id, message_text FROM chat_messages "
                                "WHERE is_page_message = 0 AND message_text LIKE :kw LIMIT 20"
                            ),
                            "params": {"kw": "%支付%"},
                            "note": "查询支付相关用户私信。",
                        }, ensure_ascii=False)
                    }
                }]
            },
        )
        httpx_mock.add_response(
            url="https://api.test.com/v1/chat/completions",
            json={"choices": [{"message": {"content": "支付相关私信 1 条。"}}]},
        )

        resp = auth_client.post("/api/sqlbot/query", json={"question": "查支付相关话题"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 1
        assert data["rows"][0]["id"] == "msg_1"
        assert "支付相关私信" in data["answer"]
