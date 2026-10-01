"""四个 Agent 节点：SchemaCurator / SQLGenerator / Executor / InsightWriter。

每个节点的职责边界（面试可讲的"单一职责"）：
- SchemaCurator：只负责"该查什么"——检索表与口径，识别歧义。不碰 SQL。
- SQLGenerator：只负责"怎么查"——生成/修正 SQL。不执行。
- Executor：只负责"安全地查"——调 MCP 工具，处理审批与错误分类。
- InsightWriter：只负责"怎么说"——结果翻译成业务结论。

节点间只通过 AgentState 通信，不直接互相调用 —— 每个节点可独立单测。
"""
from __future__ import annotations

import time
from typing import Any

from langgraph.types import interrupt

from app.agents.chat import chat_json, get_chat_fn
from app.agents.prompts import (
    INSIGHT_WRITER_SYSTEM,
    SCHEMA_CURATOR_SYSTEM,
    SQL_GENERATOR_FIX_TEMPLATE,
    SQL_GENERATOR_SYSTEM,
    render,
)
from app.agents.state import AgentState
from app.core.logging import get_logger
from app.tools.chart_gen import generate_chart
from app.tools.schema_search import search_schema
from app.tools.sql_execute import execute_sql

log = get_logger(__name__)

MAX_RETRIES = 3


def _step(node: str, started: float, **detail: Any) -> dict[str, Any]:
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"node": node, "latency_ms": latency_ms, "detail": detail}


def _fmt_schema_context(schema_context: dict[str, Any]) -> str:
    """把 schema_context 序列化进提示词（LLM 需要的全部事实）。"""
    lines = ["可用表："]
    for t in schema_context.get("tables", []):
        lines.append(f"  biz.{t['name']}({', '.join(t['columns'])})")
    metrics = schema_context.get("metrics", [])
    if metrics:
        lines.append("\n指标口径（必须遵守）：")
        for m in metrics:
            lines.append(f"  {m['metric_name']}: {m['definition']}\n    SQL提示: {m['sql_hint']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- SchemaCurator
async def schema_curator_node(state: AgentState) -> dict[str, Any]:
    """检索相关表+指标口径；发现口径歧义时 interrupt 向用户追问。"""
    started = time.perf_counter()
    question = state["question"]

    # 1. 检索 schema（关键词匹配，D2 后期升级 pgvector 语义检索）
    schema_context = await search_schema(question, top_k=5)

    # 2. LLM 分析歧义（已有澄清回答时把回答并入问题，消除歧义）
    analysis_question = question
    if state.get("clarified_answer"):
        analysis_question = f"{question}（用户已明确：{state['clarified_answer']}）"
    analysis = await chat_json([
        {"role": "system", "content": SCHEMA_CURATOR_SYSTEM},
        {
            "role": "user",
            "content": (
                f"问题：{analysis_question}\n"
                f"{schema_context.get('tables') and _fmt_schema_context(schema_context)}"
            ),
        },
    ])

    # 3. 歧义处理：未澄清过才追问（澄清过的直接放行，防止死循环）
    ambiguity = analysis.get("ambiguity")
    if ambiguity and not state.get("clarified_answer"):
        answer = interrupt({
            "type": "clarification",
            "question": ambiguity["question"],
            "options": ambiguity.get("options", []),
        })
        # ---- 恢复执行：用户回答注入状态 ----
        log.info("clarification.answered", extra={"context": {"answer": answer}})
        return {
            "schema_context": schema_context,
            "ambiguity": ambiguity,
            "clarified_answer": str(answer),
            "status": "running",
            "trace": [_step("schema_curator", started, clarified=True)],
        }

    return {
        "schema_context": schema_context,
        "ambiguity": ambiguity if ambiguity else None,
        "status": "running",
        "trace": [_step("schema_curator", started, tables=len(schema_context.get("tables", [])))],
    }


# ---------------------------------------------------------------- SQLGenerator
async def sql_generator_node(state: AgentState) -> dict[str, Any]:
    """生成 SQL；有 last_error 时进入自愈模式（错误回灌）。"""
    started = time.perf_counter()
    schema_ctx = _fmt_schema_context(state.get("schema_context", {}))

    system_prompt = render(SQL_GENERATOR_SYSTEM, schema_context=schema_ctx)
    if state.get("last_error"):
        # 自愈模式：把执行错误回灌，让 LLM 修正
        prompt = render(
            SQL_GENERATOR_FIX_TEMPLATE,
            error=state["last_error"],
            sql=state.get("sql", ""),
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    else:
        question = state["question"]
        if state.get("clarified_answer"):
            question = f"{question}（用户已明确：{state['clarified_answer']}）"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"问题：{question}"},
        ]

    result = await chat_json(messages)
    retry = state.get("retry_count", 0) + 1 if state.get("last_error") else 0
    return {
        "sql": result.get("sql", ""),
        "sql_reasoning": result.get("reasoning", ""),
        "retry_count": retry,
        "last_error": None,  # 清空错误，避免下一轮误入自愈模式
        "trace": [_step("sql_generator", started, retry=retry)],
    }


# ---------------------------------------------------------------- Executor
async def executor_node(state: AgentState) -> dict[str, Any]:
    """调 MCP sql_execute；高危 SQL interrupt 转人工审批。"""
    started = time.perf_counter()
    sql = state.get("sql") or ""
    result = await execute_sql(sql)

    if result.get("error_type") == "approval_required":
        decision = interrupt({
            "type": "approval",
            "reason": result.get("error"),
            "sql": sql,
        })
        # ---- 恢复执行：人工批准后重跑（approved=True）----
        if isinstance(decision, dict) and decision.get("approved"):
            result = await execute_sql(sql, approved=True)
        else:
            return {
                "status": "failed",
                "error": "用户拒绝了该查询",
                "trace": [_step("executor", started, approval="rejected")],
            }

    if not result.get("ok"):
        retry_count = state.get("retry_count", 0)
        if retry_count >= MAX_RETRIES:
            return {
                "status": "failed",
                "error": f"查询失败（已重试 {retry_count} 次）：{result.get('error')}",
                "trace": [_step("executor", started, retries_exhausted=True)],
            }
        # 错误回灌给 SQLGenerator 自愈
        return {
            "last_error": result.get("error", "未知错误"),
            "trace": [_step("executor", started, error=result.get("error_type"))],
        }

    return {
        "sql_result": result,
        "last_error": None,
        "status": "running",
        "trace": [_step("executor", started, row_count=result.get("row_count"))],
    }


# ---------------------------------------------------------------- InsightWriter
async def insight_writer_node(state: AgentState) -> dict[str, Any]:
    """把查询结果写成业务结论。"""
    started = time.perf_counter()
    result = state.get("sql_result") or {}
    rows = result.get("rows", [])[:10]  # 最多喂 10 行，控制 token
    result_ctx = f"列: {result.get('columns')}\n数据（前{len(rows)}行）: {rows}"

    # 结论是自由文本，不走 JSON 解析（与 SQLGenerator 的 JSON 输出不同）
    chat_fn = get_chat_fn()
    summary = await chat_fn([
        {"role": "system", "content": render(INSIGHT_WRITER_SYSTEM, result_context=result_ctx)},
        {"role": "user", "content": f"问题：{state['question']}"},
    ])

    # 图表降级策略：有结果就尝试出图；任何一步不满足条件（无数字列/超量/
    # 存储故障）都只跳过图表，绝不让图表失败炸掉整条问数链路——
    # 结论文本的价值高于图表，失败隔离是硬要求。
    chart_url = None
    chart_note = None
    try:
        chart_url, chart_note = await _maybe_chart(result, state["question"])
    except Exception as e:  # 兜底：图表是增强不是依赖
        chart_note = f"chart skipped: {type(e).__name__}"

    insight = {"summary": summary.strip(), "row_count": result.get("row_count")}
    if chart_url:
        insight["chart_url"] = chart_url
    # trace 里的 chart 字段是排障入口：出图了/为何跳过，一眼可见
    chart_detail = "generated" if chart_url else (chart_note or "skipped")
    return {
        "insight": insight,
        "status": "done",
        "trace": [_step("insight_writer", started, chart=chart_detail)],
    }


def _chart_points(result: dict) -> list[list] | None:
    """从查询结果提炼图表数据点：第一个文本列做标签，第一个数值列做值。

    只取前 10 行（图表可读性上限，与 prompt 喂 10 行一致），
    找不到数值列时返回 None（调用方跳过图表）。
    """
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if len(columns) < 2 or not rows:
        return None
    label_idx, value_idx = 0, None
    for j in range(1, len(columns)):  # 第 0 列固定做标签列
        sample = next((r[j] for r in rows if isinstance(r[j], (int, float))), None)
        if sample is not None:
            value_idx = j
            break
    if value_idx is None:
        return None
    points = []
    for r in rows[:10]:
        v = r[value_idx]
        if isinstance(v, (int, float)):
            points.append([str(r[label_idx]), v])
    return points or None


async def _maybe_chart(result: dict, question: str) -> tuple[str | None, str | None]:
    """有结构化结果就生成柱状图；返回 (url, note)。note 供 trace 排障。"""
    points = _chart_points(result)
    if points is None:
        return None, "skipped: no numeric column"
    chart = await generate_chart("bar", points, title=question[:30])
    if not chart.get("ok"):
        return None, f"skipped: {chart.get('error_type')}"
    return chart["url"], None
