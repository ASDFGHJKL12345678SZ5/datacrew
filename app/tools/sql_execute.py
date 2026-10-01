"""SQL 安全执行器：七道闸之后的最后防线。

调用链：validate_sql（七道闸）→ 只读事务 + statement_timeout + 行数上限 → 执行 → 结构化结果
纵深防御第三层：即使闸全部失效，datacrew_ro 角色本身没有写权限（DB 层兜底）。

结果结构化（供 Agent 消费）：
    {"ok": True, "columns": [...], "rows": [[...]], "row_count": N,
     "truncated": bool, "latency_ms": int, "sql": "实际执行的SQL"}
    {"ok": False, "error": "...", "error_type": "unsafe|timeout|db|approval_required"}
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal

import asyncpg

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infra.db import ro_pool
from app.tools.schema_registry import load_allowed_tables
from app.tools.sql_guard import UnsafeSQLError, validate_sql

log = get_logger(__name__)


def _jsonable(value: object) -> object:
    """asyncpg 返回值递归转成 JSON 可序列化类型（最终审查修复）。

    早期版只转顶层值：array_agg/jsonb_agg/record 里嵌套的 Decimal 原样
    漏过，到 API 层 json.dumps 才炸——合法聚合查询让 SSE 序列化崩溃，
    用户看到一个莫名的 error 事件（节点事件已经发完）。嵌套容器递归处理。
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def execute_sql(sql: str, *, approved: bool = False) -> dict:
    """安全执行 SQL。approved=True 时跳过大表审批检查（人工已批准）。"""
    s = get_settings()
    started = time.perf_counter()

    # 1. 加载白名单（Redis 缓存）
    allowed = await load_allowed_tables()

    # 2. 七道闸
    try:
        validated = validate_sql(sql, allowed, row_limit=s.sql_row_limit)
    except UnsafeSQLError as e:
        log.warning("sql.rejected", extra={"context": {"reason": str(e)}})
        return {"ok": False, "error": str(e), "error_type": "unsafe"}

    # 3. 人工审批门（大表无过滤条件）
    if validated.needs_approval and not approved:
        return {
            "ok": False,
            "error": validated.approval_reason,
            "error_type": "approval_required",
            "sql": validated.sql,
        }

    # 4. 只读事务 + 超时 + 行数上限，执行
    try:
        async with ro_pool().acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {s.sql_timeout_ms}")
                rows = await conn.fetch(validated.sql)
    except asyncpg.exceptions.QueryCanceledError:
        return {
            "ok": False,
            "error": f"查询超时（>{s.sql_timeout_ms}ms），请缩小时间范围或加过滤条件",
            "error_type": "timeout",
        }
    except asyncpg.PostgresError as e:
        # 业务错误（列名错、语法错等）—— 回灌给 Agent 驱动自愈
        return {"ok": False, "error": str(e), "error_type": "db"}
    except Exception as e:  # 连接池不可用/RuntimeError 等未预期故障
        # 早期版这些异常直接炸穿 executor 节点变成裸 SSE error；现在收敛成
        # 结构化错误，照常回灌给 Agent 走自愈/终态。
        log.exception("sql.unexpected_error")
        return {"ok": False, "error": f"查询执行失败: {e}", "error_type": "db"}

    latency_ms = int((time.perf_counter() - started) * 1000)
    truncated = len(rows) > s.sql_row_limit
    result_rows = [[_jsonable(v) for v in row.values()] for row in rows[: s.sql_row_limit]]
    columns = list(rows[0].keys()) if rows else []

    log.info(
        "sql.executed",
        extra={"context": {"row_count": len(rows), "latency_ms": latency_ms}},
    )
    return {
        "ok": True,
        "columns": columns,
        "rows": result_rows,
        "row_count": len(rows),
        "truncated": truncated,
        "latency_ms": latency_ms,
        "sql": validated.sql,
    }
