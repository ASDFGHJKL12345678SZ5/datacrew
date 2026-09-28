"""Supervisor 状态机：LangGraph 编排四 Agent + 澄清/自愈/审批三条控制流。

状态机全景：
    START -> schema_curator -> sql_generator -> executor --+
                        ^                      |             |
                        |              (错误回灌 自愈)       | (ok)
                        +----------------------+             v
                                                       insight_writer -> END
    executor --(重试耗尽/用户拒绝)--> END(failed)

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
    builder.add_edge("schema_curator", "sql_generator")
    builder.add_edge("sql_generator", "executor")
    builder.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"sql_generator": "sql_generator", "insight_writer": "insight_writer", END: END},
    )
    builder.add_edge("insight_writer", END)

    return builder.compile(checkpointer=checkpointer)


async def get_checkpointer():
    """生产 checkpointer：PostgreSQL 持久化（会话跨重启不丢）。

    采用 langgraph 官方 from_conn_string 同款配置：单连接 + autocommit。
    - autocommit=True：setup() 的迁移 SQL 含 CREATE INDEX CONCURRENTLY，
      在事务块里会报错（psycopg 默认 autocommit=False）
    - 单连接而非连接池：checkpoint 写入本来就是按 thread 串行的，
      单连接不构成瓶颈；且避开 psycopg_pool 后台 worker 在
    uvicorn 事件循环下的兼容性问题（Windows Selector 循环）
    - prepare_threshold=0：关掉 prepared statement 缓存，配合 PgBouncer 也更稳
    """
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    settings = get_settings()
    conn = await psycopg.AsyncConnection.connect(
        settings.pg_admin_dsn, autocommit=True, prepare_threshold=0
    )
    saver = AsyncPostgresSaver(conn)
    await saver.setup()
    return saver


async def build_production_graph():
    """带 PostgreSQL checkpointer 的生产状态机（API 层使用）。"""
    return build_graph(checkpointer=await get_checkpointer())
