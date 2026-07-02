"""AI-assisted read-only SQL analysis for private-message data."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import AppConfig
from app.database import get_connection

logger = logging.getLogger("uvicorn.error")

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
ALLOWED_VIEWS = {"chat_conversations", "chat_messages"}
BASE_TABLES = {"page_conversations", "conversation_messages"}
BLOCKED_SQL_WORDS = {
    "alter", "attach", "create", "delete", "detach", "drop", "insert",
    "pragma", "reindex", "replace", "update", "vacuum",
}


@dataclass(slots=True)
class SQLBotQueryResult:
    question: str
    answer: str
    sql: str
    params: dict[str, Any] | list[Any]
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    truncated: bool
    plan_note: str


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json|sql)?\s*\n?", "", text, flags=re.I)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = _strip_code_fence(text)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("AI 未返回有效的 JSON 查询计划")
        parsed = json.loads(raw[start:end + 1])
    if not isinstance(parsed, dict):
        raise RuntimeError("AI 查询计划必须是 JSON 对象")
    return parsed


class ChatSQLBotService:
    """Generate, validate, execute, and summarize read-only chat SQL."""

    def __init__(
        self,
        config: AppConfig,
        *,
        page_id: str,
        max_rows: int = 500,
        report_rows: int = 120,
    ):
        self.config = config
        self.page_id = page_id
        self.max_rows = max_rows
        self.report_rows = report_rows

    def _chat_completions_url(self) -> str:
        url = self.config.reply_api_base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions"

    async def _call_llm(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self.config.reply_enabled:
            raise RuntimeError("AI 数据分析需要先在主页概览中配置回复模型 API")

        payload = {
            "model": self.config.reply_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "messages": messages,
        }
        headers = {
            "Authorization": f"Bearer {self.config.reply_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(self._chat_completions_url(), headers=headers, json=payload)

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
            raise RuntimeError(f"AI 请求失败 ({response.status_code}): {detail}")

        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("AI 接口返回了空内容")
        return content

    def _schema_summary(self) -> str:
        return (
            "你只能查询两个已按当前主页隔离的只读视图：\n"
            "1. chat_conversations\n"
            "   - id TEXT: 会话 ID\n"
            "   - updated_time TEXT: Facebook 原始更新时间，UTC 字符串\n"
            "   - updated_at_local TEXT: 北京时间更新时间，格式 YYYY-MM-DD HH:MM:SS\n"
            "   - updated_date_local TEXT: 北京日期，格式 YYYY-MM-DD\n"
            "   - unread_count INTEGER\n"
            "   - synced_at TEXT\n"
            "2. chat_messages\n"
            "   - id TEXT: 消息 ID\n"
            "   - conversation_id TEXT: 会话 ID，可 JOIN chat_conversations.id\n"
            "   - message_text TEXT: 私信正文\n"
            "   - sender_id TEXT\n"
            "   - sender_name TEXT\n"
            "   - created_time TEXT: Facebook 原始创建时间，UTC 字符串\n"
            "   - created_at_local TEXT: 北京时间创建时间，格式 YYYY-MM-DD HH:MM:SS\n"
            "   - created_date_local TEXT: 北京日期，格式 YYYY-MM-DD\n"
            "   - synced_at TEXT\n"
            "   - is_page_message INTEGER: 1 表示主页发出的消息，0 表示用户发来的消息\n"
        )

    def _multilingual_search_guidance(self) -> str:
        return (
            "多语言话题匹配规则：\n"
            "- 用户用中文提出的话题词也要当成语义主题，不要只查中文原词。\n"
            "- 私信可能包含英语、菲律宾/Tagalog、印尼语、马来语或混合拼写；"
            "生成 SQL 时应为核心主题扩展常见同义词、动词变体、空格/连字符变体和支付渠道词。\n"
            "- 拉丁字母关键词用 LOWER(COALESCE(message_text, '')) LIKE :kwN；"
            "中文等非拉丁关键词可直接用 message_text LIKE :kwN。\n"
            "- 充值/储值/余额主题可扩展：充值, 充钱, top up, topup, top-up, recharge, reload, load, "
            "credits, credit, balance, wallet, isi saldo, isi ulang, pulsa, tambah saldo, magload, pa load。\n"
            "- 会员/订阅/VIP 主题可扩展：会员, VIP, member, membership, premium, subscribe, subscription, "
            "subscribed, renewal, renew, plan, package, langganan, berlangganan, keanggotaan, anggota, miyembro。\n"
            "- 支付/付款主题可扩展：支付, 付款, payment, pay, paid, paying, checkout, billing, bill, invoice, "
            "card, bank transfer, transfer, e-wallet, wallet, bayad, magbayad, bayar, pembayaran, dibayar, bayaran。\n"
            "- 常见支付渠道词可作为支付主题补充：GCash, Maya, PayMaya, DANA, OVO, GoPay, ShopeePay, QRIS, "
            "bank, Visa, Mastercard, PayPal。\n"
            "- 同义词不要无限扩展；围绕用户问题选择最相关的 10-40 个条件，优先召回，报告阶段再归类和剔除噪音。\n"
        )

    def _build_sql_plan_prompt(self, question: str, now: datetime) -> list[dict[str, str]]:
        current_time = now.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        current_date = now.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
        system = (
            "你是 FBIManager 的私信数据库 SQL 分析助手。"
            "根据用户问题生成一个 SQLite 只读查询计划。"
            "不要回答问题本身，只返回 JSON。"
        )
        user = (
            f"当前时区: Asia/Shanghai\n当前北京时间: {current_time}\n当前日期: {current_date}\n\n"
            f"{self._schema_summary()}\n"
            f"{self._multilingual_search_guidance()}\n"
            "规则：\n"
            "- 只允许 SELECT 或 WITH 查询，只能使用 chat_conversations 和 chat_messages。\n"
            "- 不要查询真实表名，不要写入、建表、PRAGMA、ATTACH 或多语句。\n"
            "- 涉及自然语言日期时使用 created_at_local / created_date_local；"
            "“今天”表示今天 00:00 到当前时间，“昨天到今天”表示昨天 00:00 到当前时间。\n"
            "- 分析用户咨询内容时通常过滤 is_page_message = 0，除非用户明确要求看主页回复。\n"
            "- 关键词搜索使用 LIKE 命名参数，例如 message_text LIKE :kw0。\n"
            "- 使用命名参数，不要把用户输入直接拼到 SQL 字符串里。\n"
            "- 对相关话题分析，优先返回 message_text、sender_name、created_at_local、conversation_id；"
            "关联会话时可 JOIN chat_conversations。\n"
            "- 查询明细时加 ORDER BY created_at_local DESC，避免结果顺序不稳定。\n"
            f"- 默认 LIMIT 不超过 {self.max_rows} 行；需要统计时优先 GROUP BY / COUNT。\n\n"
            "返回 JSON 格式：\n"
            '{"sql":"SELECT ...","params":{"start":"YYYY-MM-DD HH:MM:SS"},"note":"一句话说明查询口径"}\n\n'
            f"用户问题：{question}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _normalize_sql(self, sql: str) -> str:
        sql = _strip_code_fence(sql)
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            raise RuntimeError("AI 未生成 SQL")
        return sql

    def _validate_sql(self, sql: str) -> None:
        compact = re.sub(r"\s+", " ", sql).strip().lower()
        if not (compact.startswith("select ") or compact.startswith("with ")):
            raise RuntimeError("只允许 SELECT/WITH 只读查询")
        if ";" in compact:
            raise RuntimeError("不允许执行多语句 SQL")

        for word in BLOCKED_SQL_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", compact):
                raise RuntimeError(f"SQL 包含不允许的关键字: {word}")

        for table in BASE_TABLES:
            if re.search(rf"\b{re.escape(table)}\b", compact):
                raise RuntimeError("SQL 只能查询隔离后的 chat_* 视图，不能访问原始表")

        if not any(re.search(rf"\b{view}\b", compact) for view in ALLOWED_VIEWS):
            raise RuntimeError("SQL 必须查询 chat_conversations 或 chat_messages")

    def _sqlite_literal(self, connection: sqlite3.Connection, value: str) -> str:
        return str(connection.execute("SELECT quote(?)", (value,)).fetchone()[0])

    def _create_scoped_views(self, connection: sqlite3.Connection) -> None:
        page_literal = self._sqlite_literal(connection, self.page_id)
        connection.executescript(
            f"""
            DROP VIEW IF EXISTS temp.chat_conversations;
            DROP VIEW IF EXISTS temp.chat_messages;

            CREATE TEMP VIEW chat_conversations AS
            SELECT
                id,
                updated_time,
                datetime(REPLACE(SUBSTR(updated_time, 1, 19), 'T', ' '), '+8 hours') AS updated_at_local,
                date(datetime(REPLACE(SUBSTR(updated_time, 1, 19), 'T', ' '), '+8 hours')) AS updated_date_local,
                unread_count,
                synced_at
            FROM page_conversations
            WHERE page_id = {page_literal};

            CREATE TEMP VIEW chat_messages AS
            SELECT
                m.id,
                m.conversation_id,
                m.message_text,
                m.sender_id,
                m.sender_name,
                m.created_time,
                datetime(REPLACE(SUBSTR(m.created_time, 1, 19), 'T', ' '), '+8 hours') AS created_at_local,
                date(datetime(REPLACE(SUBSTR(m.created_time, 1, 19), 'T', ' '), '+8 hours')) AS created_date_local,
                m.synced_at,
                CASE WHEN m.sender_id = {page_literal} THEN 1 ELSE 0 END AS is_page_message
            FROM conversation_messages m
            JOIN page_conversations c ON c.id = m.conversation_id
            WHERE c.page_id = {page_literal};
            """
        )

    def _install_authorizer(self, connection: sqlite3.Connection) -> None:
        allowed_internal_tables = {"sqlite_master", "sqlite_temp_master"}

        def authorize(action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                table = arg1 or ""
                if table in ALLOWED_VIEWS or table in allowed_internal_tables:
                    return sqlite3.SQLITE_OK
                if table in BASE_TABLES and source in ALLOWED_VIEWS:
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)

    def _execute_sql(self, sql: str, params: dict[str, Any] | list[Any]) -> tuple[list[dict[str, Any]], list[str], bool]:
        self._validate_sql(sql)
        with get_connection() as connection:
            self._create_scoped_views(connection)
            connection.execute("PRAGMA query_only = ON")
            self._install_authorizer(connection)

            deadline = time.monotonic() + 8.0

            def abort_if_slow() -> int:
                return 1 if time.monotonic() > deadline else 0

            connection.set_progress_handler(abort_if_slow, 10_000)
            try:
                bind_params: dict[str, Any] | tuple[Any, ...]
                if isinstance(params, list):
                    bind_params = tuple(params)
                elif isinstance(params, dict):
                    bind_params = params
                else:
                    raise RuntimeError("SQL 参数必须是对象或数组")

                cursor = connection.execute(sql, bind_params)
                columns = [item[0] for item in (cursor.description or [])]
                fetched = cursor.fetchmany(self.max_rows + 1)
            except sqlite3.Error as exc:
                raise RuntimeError(f"SQL 执行失败: {exc}") from exc
            finally:
                connection.set_authorizer(None)
                connection.set_progress_handler(None, 0)

        truncated = len(fetched) > self.max_rows
        rows = [dict(row) for row in fetched[:self.max_rows]]
        return rows, columns, truncated

    def _rows_for_report(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        limited = rows[:self.report_rows]
        normalized: list[dict[str, Any]] = []
        for row in limited:
            item: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, str) and len(value) > 500:
                    item[key] = value[:500] + "..."
                else:
                    item[key] = value
            normalized.append(item)
        return normalized

    def _build_report_prompt(
        self,
        *,
        question: str,
        sql: str,
        params: dict[str, Any] | list[Any],
        rows: list[dict[str, Any]],
        columns: list[str],
        truncated: bool,
        plan_note: str,
        now: datetime,
    ) -> list[dict[str, str]]:
        result_payload = {
            "columns": columns,
            "row_count_returned": len(rows),
            "truncated": truncated,
            "rows": self._rows_for_report(rows),
        }
        system = (
            "你是私信业务数据分析师。基于 SQL 查询结果生成中文分析报告。"
            "只能使用给定结果，不要编造未查询的数据。"
        )
        user = (
            f"当前北京时间: {now.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"用户问题: {question}\n"
            f"查询口径: {plan_note or '未说明'}\n"
            f"SQL: {sql}\n"
            f"Params: {json.dumps(params, ensure_ascii=False)}\n"
            f"Result: {json.dumps(result_payload, ensure_ascii=False)}\n\n"
            "请输出结构清晰的中文报告，包含：\n"
            "1. 结论摘要\n"
            "2. 数据范围与口径\n"
            "3. 关键发现（数量、趋势、集中话题、典型表述）\n"
            "4. 建议动作\n"
            "如果没有结果，明确说明未查到匹配私信，并给出可能的排查方向。"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def answer(self, question: str, *, now: datetime | None = None) -> SQLBotQueryResult:
        question = question.strip()
        if not question:
            raise RuntimeError("请输入要分析的问题")
        if len(question) > 2000:
            raise RuntimeError("问题过长，请控制在 2000 字以内")

        now = now or datetime.now(LOCAL_TZ)
        plan_content = await self._call_llm(
            self._build_sql_plan_prompt(question, now),
            temperature=0.05,
            max_tokens=1400,
        )
        plan = _extract_json_object(plan_content)
        sql = self._normalize_sql(str(plan.get("sql", "")))
        params = plan.get("params") or {}
        if not isinstance(params, (dict, list)):
            raise RuntimeError("AI 返回的 SQL 参数格式无效")

        rows, columns, truncated = self._execute_sql(sql, params)
        report = await self._call_llm(
            self._build_report_prompt(
                question=question,
                sql=sql,
                params=params,
                rows=rows,
                columns=columns,
                truncated=truncated,
                plan_note=str(plan.get("note", "")),
                now=now,
            ),
            temperature=0.2,
            max_tokens=1800,
        )

        return SQLBotQueryResult(
            question=question,
            answer=report.strip(),
            sql=sql,
            params=params,
            rows=rows,
            columns=columns,
            row_count=len(rows),
            truncated=truncated,
            plan_note=str(plan.get("note", "")),
        )
