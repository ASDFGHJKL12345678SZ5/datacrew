"""问数用例：把状态机封装成"问一次/恢复一次"两个用例，API 层只调这里。

分层理由（整洁架构）：API 层不懂 LangGraph，Agent 层不懂 HTTP ——
用例层是两者间唯一的翻译官。将来加 WebSocket / 加定时批量问数，只加用例不加端点。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langgraph.types import Command

from app.agents.supervisor import build_production_graph
from app.core.logging import get_logger

log = get_logger(__name__)

_graph = None
_graph_lock = asyncio.Lock()


async def _get_graph():
    """生产状态机单例（PostgresSaver 构建有 IO，加锁防并发重复构建）。"""
    global _graph
    if _graph is None:
        async with _graph_lock:
            if _graph is None:
                _graph = await build_production_graph()
    return _graph


def _config(session_id: str) -> dict[str, Any]:
    # thread_id = session_id：同一会话的状态在 checkpointer 里跨请求延续
    return {"configurable": {"thread_id": session_id}}


async def _node_events(
    chunks: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """把 astream(updates) 的 chunk 流转成 node_done 事件。

    langgraph 1.x 的 updates 模式：普通节点产出 {节点名: 状态增量dict}；
    中断时产出 {'__interrupt__': (Interrupt(...),)} —— 值是 tuple，
    且中断点没有完整节点更新。中断事件统一由 _events_after_stream 从
    aget_state 读取，这里跳过即可。
    """
    async for chunk in chunks:
        for node, update in chunk.items():
            if node == "__interrupt__":
                continue
            step = (update.get("trace") or [{}])[-1]
            yield {
                "event": "node_done",
                "node": node,
                "latency_ms": step.get("latency_ms"),
                "detail": step.get("detail", {}),
            }


async def _events_after_stream(graph, config) -> AsyncIterator[dict[str, Any]]:
    """流结束后判定终态：中断（澄清/审批）还是出结果。"""
    state = await graph.aget_state(config)
    for task in state.tasks:
        if task.interrupts:
            payload = task.interrupts[0].value
            yield {"event": payload["type"], **payload}
            return
    values = state.values
    if values.get("status") == "failed":
        yield {"event": "error", "message": values.get("error", "查询失败")}
        return
    yield {
        "event": "result",
        "summary": (values.get("insight") or {}).get("summary", ""),
        "sql": values.get("sql"),
        "columns": (values.get("sql_result") or {}).get("columns"),
        "rows": (values.get("sql_result") or {}).get("rows"),
        "row_count": (values.get("sql_result") or {}).get("row_count"),
        "chart_url": (values.get("insight") or {}).get("chart_url"),
        "retry_count": values.get("retry_count", 0),
    }


async def ask(question: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
    """问一次：驱动状态机跑到底或跑到中断点，全程产出 SSE 事件。"""
    graph = await _get_graph()
    log.info("ask.start", extra={"context": {"session_id": session_id}})
    async for ev in _node_events(
        graph.astream(
            {"question": question, "session_id": session_id, "status": "running"},
            _config(session_id),
            stream_mode="updates",
        )
    ):
        yield ev
    async for ev in _events_after_stream(graph, _config(session_id)):
        yield ev


async def resume(session_id: str, value: Any) -> AsyncIterator[dict[str, Any]]:
    """恢复执行：把用户的澄清回答 / 审批决定注入中断点，继续跑。"""
    graph = await _get_graph()
    log.info("ask.resume", extra={"context": {"session_id": session_id}})
    async for ev in _node_events(
        graph.astream(
            Command(resume=value), _config(session_id), stream_mode="updates"
        )
    ):
        yield ev
    async for ev in _events_after_stream(graph, _config(session_id)):
        yield ev
