"""Agent 状态定义：多 Agent 之间唯一的数据契约。

设计原则（面试可讲）：
1. 状态即契约：四个 Agent 只通过状态字段通信，不直接互相调用 —— 可单测、可替换
2. 每步产物落字段：schema_context / sql / sql_result / insight 各自独立，
   任何一步失败都能从状态里看到"断在哪"
3. trace 记录每步耗时与工具调用：成本核算与压测报告的数据源
4. status 是状态机的"仪表盘"：API 层靠它决定给用户返回什么（澄清问题/审批请求/最终结果）
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class Ambiguity(TypedDict, total=False):
    """指标口径歧义（如"销售额" = GMV 还是实付）。"""

    term: str            # 歧义术语
    options: list[str]   # 候选口径
    question: str        # 向用户追问的话术


class Approval(TypedDict, total=False):
    """高危 SQL 的人工审批状态。"""

    needed: bool
    reason: str
    approved: bool


class TraceStep(TypedDict, total=False):
    node: str
    latency_ms: int
    detail: dict[str, Any]


class AgentState(TypedDict, total=False):
    # ---- 输入 ----
    question: str
    session_id: str
    clarified_answer: str | None   # 用户对澄清问题的回答（resume 时注入）

    # ---- SchemaCurator 产物 ----
    schema_context: dict[str, Any]     # {"tables": [...], "metrics": [...]}
    ambiguity: Ambiguity | None
    data_as_of: str | None            # 数据截止日：相对时间的推算锚点（不是系统今天）

    # ---- SQLGenerator 产物 ----
    sql: str | None
    sql_reasoning: str | None

    # ---- Executor 产物 ----
    sql_result: dict[str, Any] | None
    last_error: str | None
    retry_count: int

    # ---- 人工审批 ----
    approval: Approval | None

    # ---- InsightWriter 产物 ----
    insight: dict[str, Any] | None     # {"summary": str, "chart_data": [...]}

    # ---- 拒答（元数据类问题：表结构/列数/索引，正确答法被表白名单禁止）----
    refusal: str | None                # 拒答话术；非错误，是正常终态（status=done）

    # ---- 全程 ----
    status: str                        # running|clarifying|awaiting_approval|done|failed
    error: str | None                  # 终态失败原因（给用户看的话术）
    # operator.add 归并器：各节点返回的步骤自动追加，互不覆盖
    trace: Annotated[list[TraceStep], operator.add]
