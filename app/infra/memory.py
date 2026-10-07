"""Agent 记忆层：会话级**偏好记忆**（长期记忆的最小可用落地）。

分层定位（与"工作记忆 / 短期记忆 / 长期记忆"框架对齐）：
- 工作记忆：节点内当场组装 prompt（schema_context / 证据块），不落盘；
- 短期记忆：LangGraph checkpointer（PG）——任务状态，跨请求可恢复；
- 长期记忆：**本模块**——从交互中抽取并沉淀的用户偏好，跨轮次复用。

为什么只做"偏好"这一种长期记忆（而不是"把对话摘要塞进向量库"）：
偏好是唯一**结构化可得**的记忆——澄清中断的 payload 已经带 term/options，
用户的选择就是 (term → choice) 这一条记录。**抽取不需要 LLM，一条 upsert 即落库**。
为记忆而记忆（做没有召回场景的对话向量库）只会增加故障面。

四条设计约束（面试可讲）：
1. **作用域 = session_id**：系统暂无用户体系，会话是能拿到的最稳定标识。
   真实系统换成 user_id，改造点只有这一个参数。
2. **不进入 Agent 的可读边界**：mem schema **不授权**给 datacrew_ro——Agent 生成的
   SQL 永远读不到记忆表（注入由应用层完成，见 nodes.schema_curator_node）。
   记忆是"应用替 Agent 记住"，不是"Agent 自己去查记忆"。
3. **全链路降级**：读/写失败一律 fail-soft——记忆是增强不是依赖，与
   cache.py / observability.py 同款纪律（记忆库挂了最坏是重新追问一次）。
4. **表由应用启动时幂等建**（ensure_memory_schema）：不放进 deploy/init/*.sql，
   这样已存在的开发库/CI 库无需手工 DDL 就能升级，也避免同一份 DDL 两处维护。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.infra.db import admin_pool

log = get_logger(__name__)

# 记忆表 DDL（幂等）。放这里的理由见模块 docstring 第 4 条。
_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS mem;
CREATE TABLE IF NOT EXISTS mem.preferences (
    id         BIGSERIAL PRIMARY KEY,
    session_id TEXT        NOT NULL,
    term       TEXT        NOT NULL,
    choice     TEXT        NOT NULL,
    source     TEXT        NOT NULL DEFAULT 'clarification',
    hit_count  INTEGER     NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT preferences_session_term_key UNIQUE (session_id, term)
);
CREATE INDEX IF NOT EXISTS preferences_session_idx ON mem.preferences (session_id);
"""


async def ensure_memory_schema() -> bool:
    """幂等建表（应用启动调用）。失败不抛：记忆不可用时系统仍要能问数。"""
    try:
        async with admin_pool().acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        return True
    except Exception as e:
        log.warning("memory.ensure_failed", extra={"context": {"reason": str(e)[:120]}})
        return False


async def load_preferences(session_id: str) -> dict[str, str]:
    """读本会话已沉淀的偏好 {term: choice}；任何异常返回空 dict（降级=当没记忆）。"""
    if not session_id:
        return {}
    try:
        async with admin_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT term, choice FROM mem.preferences WHERE session_id = $1", session_id
            )
        return {r["term"]: r["choice"] for r in rows}
    except Exception as e:
        log.warning("memory.load_degraded", extra={"context": {"reason": str(e)[:120]}})
        return {}


async def remember_preference(
    session_id: str, term: str, choice: str, source: str = "clarification"
) -> bool:
    """沉淀一条偏好（同 term 覆盖 choice，保留 hit_count）。失败静默返回 False。"""
    if not session_id or not term or not choice:
        return False
    try:
        async with admin_pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mem.preferences (session_id, term, choice, source)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (session_id, term) DO UPDATE
                   SET choice = EXCLUDED.choice,
                       source = EXCLUDED.source,
                       updated_at = now()
                """,
                session_id, term, choice, source,
            )
        log.info("memory.remembered", extra={"context": {"term": term, "choice": choice[:40]}})
        return True
    except Exception as e:
        log.warning("memory.remember_degraded", extra={"context": {"reason": str(e)[:120]}})
        return False


async def mark_preference_used(session_id: str, term: str) -> None:
    """命中偏好时累加复用次数（可观测：这条记忆被用过几次）。失败静默。"""
    try:
        async with admin_pool().acquire() as conn:
            await conn.execute(
                "UPDATE mem.preferences SET hit_count = hit_count + 1, updated_at = now() "
                "WHERE session_id = $1 AND term = $2",
                session_id, term,
            )
    except Exception as e:
        log.warning("memory.touch_degraded", extra={"context": {"reason": str(e)[:120]}})


async def forget_preferences(session_id: str) -> int:
    """清空某会话的记忆（测试清理用；线上对应"清空会话"）。返回删除行数。"""
    try:
        async with admin_pool().acquire() as conn:
            rows = await conn.fetch(
                "DELETE FROM mem.preferences WHERE session_id = $1 RETURNING id", session_id
            )
        return len(rows)
    except Exception as e:
        log.warning("memory.forget_degraded", extra={"context": {"reason": str(e)[:120]}})
        return 0


async def memory_stats(session_id: str) -> dict[str, Any]:
    """会话记忆概览（/health 或调试端点可用）：条数与总复用次数。"""
    try:
        async with admin_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT count(*) AS n, coalesce(sum(hit_count), 0) AS hits "
                "FROM mem.preferences WHERE session_id = $1",
                session_id,
            )
        return {"terms": int(row["n"]), "hits": int(row["hits"])}
    except Exception:
        return {"terms": 0, "hits": 0}


# ---------------------------------------------------------------- 口径词干判定
_GENERIC_SEPS = ("（", "(", "，", ",", "：", ":", " ", "－", "-", "—", "/")


def choice_stem(choice: str) -> str:
    """把口径选项压成"可被问题命中的词干"。

    '实付销售额（支付成功订单的实付金额）' -> '实付'
    'GMV（成交总额，含取消/退款单）'        -> 'gmv'
    用途：判断用户本轮是否已经自己把口径说出口（说出口就不套历史偏好）。
    """
    c = (choice or "").strip()
    for sep in _GENERIC_SEPS:
        c = c.split(sep, 1)[0]
    head = c.strip()
    if not head:
        return ""
    return head.lower() if head.isascii() else head[:2]


def explicitly_stated(question: str, choice: str) -> bool:
    """问题里是否已经显式说出该口径（本轮表述优先于历史偏好）。"""
    stem = choice_stem(choice)
    if not stem:
        return False
    return stem in (question or "").replace(" ", "").lower()
