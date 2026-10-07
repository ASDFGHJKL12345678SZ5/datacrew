"""Supervisor 状态机：LangGraph 编排四 Agent + 澄清/自愈/审批三条控制流。

状态机全景：
    START -> schema_curator -> sql_generator -> executor --+
                        ^                      |             |
                        |              (错误回灌 自愈)       | (ok)
                        +----------------------+             v
                                                       insight_writer -> END
    executor --(重试耗尽/用户拒绝)--> END(failed)
    schema_curator --(元数据类问题)--> END(refused：拒答话术走 result.summary)

三条控制流（本项目的面试核心叙事）：
    1. 澄清：schema_curator 内 interrupt() —— 口径歧义时暂停，追问用户后恢复
    2. 自愈：executor 错误 -> 条件边回到 sql_generator（错误回灌），最多 3 轮
    3. 审批：executor 内 interrupt() —— 大表无过滤查询暂停，人工批准后带 approved 重跑

为什么用 LangGraph 而不是自己写 while 循环（面试可讲）：
    - interrupt/resume 是框架原语：human-in-the-loop 不用自己造协程状态保存
    - PostgreSQL checkpointer：刷新页面/服务重启后会话不丢（状态在数据库里）
    - 条件边显式声明控制流，图即可视化，评审者一眼看懂
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.agents.nodes import (
    executor_node,
    insight_writer_node,
    schema_curator_node,
    sql_generator_node,
)
from app.agents.state import AgentState
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

Route = Literal["sql_generator", "insight_writer", "__end__"]
CuratorRoute = Literal["sql_generator", "__end__"]


def _route_after_curator(state: AgentState) -> CuratorRoute:
    """curator 之后：元数据类问题已拒答，直接终止（不生成 SQL、不碰数据库）。

    新增这条边的理由：拒答是**终态**，不能继续走 sql_generator——否则模型会为
    "有多少列"编一条 COUNT(*) 并答出错误数字（真实模型实测过）。
    """
    if state.get("refusal"):
        return END
    return "sql_generator"


def _route_after_executor(state: AgentState) -> Route:
    """executor 之后的条件路由：失败终态 / 自愈回炉 / 进入解读。"""
    if state.get("status") == "failed":
        return END
    if state.get("last_error"):
        # 错误回灌自愈（retry_count 已达上限时 executor 已直接置 failed）
        return "sql_generator"
    return "insight_writer"


def build_graph(checkpointer=None):
    """组装状态机。checkpointer 由调用方注入：测试传 None，生产传 PostgresSaver。"""
    builder = StateGraph(AgentState)

    builder.add_node("schema_curator", schema_curator_node)
    builder.add_node("sql_generator", sql_generator_node)
    builder.add_node(
        "executor",
        executor_node,
        # 节点级重试：瞬时故障（LLM 超时/限流）由框架重试，与 tenacity 的应用层重试互补
        retry_policy=RetryPolicy(max_attempts=2),
    )
    builder.add_node("insight_writer", insight_writer_node)

    builder.add_edge(START, "schema_curator")
    builder.add_conditional_edges(
        "schema_curator",
        _route_after_curator,
        {"sql_generator": "sql_generator", END: END},
    )
    builder.add_edge("sql_generator", "executor")
    builder.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"sql_generator": "sql_generator", "insight_writer": "insight_writer", END: END},
    )
    builder.add_edge("insight_writer", END)

    return builder.compile(checkpointer=checkpointer)


_checkpointer_pool = None  # 供 lifespan 关闭


async def close_checkpointer() -> None:
    """关闭 checkpointer 连接池（lifespan 停机时调用）。"""
    global _checkpointer_pool
    if _checkpointer_pool is not None:
        await _checkpointer_pool.close()
        _checkpointer_pool = None


async def get_checkpointer():
    """生产 checkpointer：PostgreSQL 持久化（会话跨重启不丢）。

    D3 压测后从单连接改为 AsyncConnectionPool（2-10 条），三级优化均有实测：
    1. executemany 逐行化（FastAsyncPostgresSaver）：psycopg 异步 executemany
       走 pipeline 协议，每次同步约 22ms 固定成本，一次 invoke 11 次写入全中招
    2. supports_pipeline=False：同上，pipeline 在串行场景只有税没有收益
    3. 池 + 全局锁替换：单连接下所有 checkpoint 操作串在一条连接上，QPS 锁死
       3.9；池模式下 FastAsyncPostgresSaver 把父类全局锁换成空锁，QPS 随
       并发扩展（实测并发 5/10 时 52/56，单并发 P50 55ms）
    - autocommit=True：setup() 的迁移 SQL 含 CREATE INDEX CONCURRENTLY，
      在事务块里会报错（经 kwargs 下传给每条连接）
    - prepare_threshold=0：关掉 prepared statement 缓存，配合 PgBouncer 也更稳
    """
    from psycopg_pool import AsyncConnectionPool

    from app.infra.checkpointer import FastAsyncPostgresSaver

    global _checkpointer_pool
    settings = get_settings()
    pool = AsyncConnectionPool(
        conninfo=settings.pg_admin_dsn,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await pool.open()
    saver = FastAsyncPostgresSaver(pool)
    saver.supports_pipeline = False
    await saver.setup()
    _checkpointer_pool = pool
    return saver


async def build_production_graph():
    """带 PostgreSQL checkpointer 的生产状态机（API 层使用）。"""
    return build_graph(checkpointer=await get_checkpointer())
