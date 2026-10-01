"""四个 Agent 节点：SchemaCurator / SQLGenerator / Executor / InsightWriter。

每个节点的职责边界（面试可讲的"单一职责"）：
- SchemaCurator：只负责"该查什么"——检索表与口径，识别歧义。不碰 SQL。
- SQLGenerator：只负责"怎么查"——生成/修正 SQL。不执行。
- Executor：只负责"安全地查"——调 MCP 工具，处理审批与错误分类。
- InsightWriter：只负责"怎么说"——结果翻译成业务结论。

节点间只通过 AgentState 通信，不直接互相调用 —— 每个节点可独立单测。
"""
from __future__ import annotations

import datetime as _dt
import json
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
from app.infra.db import admin_pool  # noqa: TID252  数据截止日查询
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


def _today_cn() -> str:
    """当前日期的中文可读形式（如 "2026-10-01 星期四"）——注入 LLM 推算相对时间。"""
    now = _dt.datetime.now()
    return now.strftime("%Y-%m-%d 星期") + "一二三四五六日"[now.weekday()]


async def _data_as_of() -> str:
    """数据截止日 = orders.max(created_at) 的日期。

    真实评测事故："昨天有多少订单"模型按系统今天推算，金标按数据截止日推算，
    两者差 3 天直接判错。BI 系统的正确口径就是按数据截止日解释相对时间，
    系统今天只回答"现在几点"这类问题。查询只读、索引友好。
    """
    try:
        async with admin_pool().acquire() as conn:
            return str(await conn.fetchval("SELECT max(created_at)::date FROM biz.orders"))
    except Exception as e:  # 降级：拿不到用系统今天，不阻塞主链路
        log.warning("data_as_of.fallback", extra={"context": {"reason": str(e)[:100]}})
        return _dt.date.today().isoformat()


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
    # 当前日期注入：相对时间（昨天/最近7天）必须可推算，否则真实模型会以
    # "不知道今天几号"为由追问（真实评测 22/25 触发追问的事故之一）
    today = _today_cn()
    as_of = await _data_as_of()
    analysis = await chat_json([
        {"role": "system", "content": (
            SCHEMA_CURATOR_SYSTEM
            + f"\n当前日期：{today}"
            + f"\n数据统计截止日：{as_of}（相对时间一律按它推算，不是系统今天）"
        )},
        {
            "role": "user",
            "content": (
                f"问题：{analysis_question}\n"
                f"{schema_context.get('tables') and _fmt_schema_context(schema_context)}"
            ),
        },
    ], tier="small")  # 歧义检测是轻任务（结构化小输出），走轻量模型——分级路由的真实调用点

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
            "data_as_of": as_of,
            "status": "running",
            "trace": [_step("schema_curator", started, clarified=True)],
        }

    return {
        "schema_context": schema_context,
        "ambiguity": ambiguity if ambiguity else None,
        "data_as_of": as_of,
        "status": "running",
        "trace": [_step("schema_curator", started, tables=len(schema_context.get("tables", [])))],
    }


# ---------------------------------------------------------------- SQLGenerator
async def sql_generator_node(state: AgentState) -> dict[str, Any]:
    """生成 SQL；有 last_error 时进入自愈模式（错误回灌）。"""
    started = time.perf_counter()
    schema_ctx = _fmt_schema_context(state.get("schema_context", {}))

    system_prompt = render(SQL_GENERATOR_SYSTEM, schema_context=schema_ctx)
    as_of = state.get("data_as_of") or ""
    if as_of:
        system_prompt += (
            f"\n数据统计截止日：{as_of}。"
            "昨天/最近N天/上个月等相对时间一律按数据截止日推算，"
            f"（如昨天= {as_of} 前一天）。"
            "问题没提时间范围时，WHERE 禁止出现任何时间条件——默认全量统计。"
        )
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

    try:
        result = await chat_json(messages)
    except json.JSONDecodeError:
        # 真实模型偶发把 JSON 包在自然语言里或输出被截断（评测实测：
        # "各城市的订单量排名" 23s 自愈失败于此）。立即原样重来一次，
        # 追加硬约束——比走面向 SQL 错误的自愈回路更对症。
        result = await chat_json(
            messages + [{"role": "user", "content": "严格只输出一个 JSON 对象，不要任何其他文字"}]
        )
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
# token 预算（README §8 承诺的实现）：单请求最多喂 10 行；单格超 200 字符
# 截断；整段上下文超 4000 字符再截断——超限时 log.warning 告警。
# 防的是"单行 944KB 的 array_agg 把 prompt 撑爆"这类真实事故。
MAX_CELL_CHARS = 200
MAX_CONTEXT_CHARS = 4000


def _fmt_rows_for_llm(columns: list[str] | None, rows: list) -> str:
    """把查询结果格式化成给 LLM 的上下文（带 token 预算截断与告警）。"""
    clipped = [[str(c)[:MAX_CELL_CHARS] for c in row] for row in rows]
    text = f"列: {columns}\n数据（前{len(clipped)}行）: {clipped}"
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "…[超token预算已截断]"
        log.warning("insight.token_budget_exceeded", extra={"context": {"rows": len(clipped)}})
    elif any(len(str(c)) > MAX_CELL_CHARS for row in rows for c in row):
        log.warning("insight.cell_truncated", extra={"context": {"limit": MAX_CELL_CHARS}})
    return text

async def insight_writer_node(state: AgentState) -> dict[str, Any]:
    """把查询结果写成业务结论。"""
    started = time.perf_counter()
    result = state.get("sql_result") or {}
    rows = result.get("rows", [])[:10]  # 最多喂 10 行，控制 token
    result_ctx = _fmt_rows_for_llm(result.get("columns"), rows)

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
