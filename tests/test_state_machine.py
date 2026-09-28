"""状态机集成测试：澄清 / 自愈 / 审批 / 正常流，四条控制流全覆盖。

这些测试用 Mock LLM（LLM_MODE=mock）+ 真实数据库 + 内存 checkpointer ——
CI 不需要 API Key。每条控制流对应 README 里的一条面试叙事，测试即证据。
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.supervisor import build_graph
from app.core.config import get_settings
from app.infra.cache import close_redis
from app.infra.db import close_pools, init_pools

pytestmark = pytest.mark.usefixtures("db_pools")


def _graph():
    # interrupt/resume 的暂停状态必须由 checkpointer 持久化：
    # 测试用 InMemorySaver，生产用 PostgresSaver（见 supervisor.get_checkpointer）
    return build_graph(checkpointer=InMemorySaver())


@pytest.fixture()
async def db_pools():
    # 每个测试独立的 PG 池 + Redis 连接（跨事件循环复用会报 Event loop is closed）
    await init_pools()
    yield
    await close_pools()
    await close_redis()


async def _pending_interrupt(graph, config) -> dict | None:
    # 从图状态里取当前中断 payload（澄清问题 / 审批请求）
    state = await graph.aget_state(config)
    for task in state.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


class TestClarificationFlow:
    async def test_ambiguity_triggers_interrupt_then_completes(self) -> None:
        # 口径歧义 -> interrupt 追问 -> 用户回答 -> 恢复执行 -> 完成
        graph = _graph()
        config = {"configurable": {"thread_id": "t-clarify"}}

        # 第一遍：应在 schema_curator 处中断
        await graph.ainvoke(
            {"question": "上个月各渠道的销售额是多少", "session_id": "s1", "status": "running"},
            config,
        )
        payload = await _pending_interrupt(graph, config)
        assert payload is not None and payload["type"] == "clarification"
        assert "GMV" in payload["question"]

        # 恢复：用户回答"实付销售额"
        final = await graph.ainvoke(Command(resume="实付销售额"), config)
        assert final["status"] == "done"
        assert final.get("clarified_answer") == "实付销售额"
        assert final.get("insight")


class TestSelfHealingFlow:
    async def test_bad_sql_is_corrected_via_error_feedback(self) -> None:
        # Mock 首先生成错误列名 -> 闸拦截 -> 错误回灌 -> 修正后成功
        # （"销售额"问题会先触发澄清中断，先应答，自愈在恢复后的流程中发生）
        graph = _graph()
        config = {"configurable": {"thread_id": "t-heal"}}

        await graph.ainvoke(
            {"question": "上个月各渠道的销售额是多少", "session_id": "s2", "status": "running"},
            config,
        )
        final = await graph.ainvoke(Command(resume="实付销售额"), config)
        assert final["status"] == "done"
        # 自愈发生过：retry_count >= 1，且最终 SQL 用的是真实列 pay_amount
        assert final["retry_count"] >= 1
        assert "pay_amount" in final["sql"]
        assert "sale_amount" not in final["sql"]
        # 执行成功有真实数据
        assert final["sql_result"]["row_count"] == 3


class TestApprovalFlow:
    async def test_big_table_scan_requires_approval(self) -> None:
        # 大表无过滤查询 -> interrupt 审批 -> 批准 -> 执行（自动 LIMIT）
        graph = _graph()
        config = {"configurable": {"thread_id": "t-approval"}}

        await graph.ainvoke(
            {"question": "给我所有订单列表", "session_id": "s3", "status": "running"},
            config,
        )
        payload = await _pending_interrupt(graph, config)
        assert payload is not None and payload["type"] == "approval"
        assert payload["sql"].startswith("SELECT")

        # 批准后恢复
        final = await graph.ainvoke(Command(resume={"approved": True}), config)
        assert final["status"] == "done"
        assert final["sql_result"]["row_count"] <= get_settings().sql_row_limit

    async def test_rejection_fails_gracefully(self) -> None:
        # 拒绝审批 -> 优雅失败（有面向用户的错误话术，不是堆栈）
        graph = _graph()
        config = {"configurable": {"thread_id": "t-reject"}}

        await graph.ainvoke(
            {"question": "给我所有订单列表", "session_id": "s4", "status": "running"},
            config,
        )
        final = await graph.ainvoke(Command(resume={"approved": False}), config)
        assert final["status"] == "failed"
        assert "拒绝" in final["error"]


class TestHappyPath:
    async def test_unambiguous_question_completes_directly(self) -> None:
        # 无歧义问题 -> 无中断 -> 直达完成
        graph = _graph()
        config = {"configurable": {"thread_id": "t-happy"}}

        final = await graph.ainvoke(
            {"question": "上个月有多少下单用户", "session_id": "s5", "status": "running"},
            config,
        )
        assert final["status"] == "done"
        assert await _pending_interrupt(graph, config) is None
        # trace 记录四个节点全部执行
        nodes = [s["node"] for s in final["trace"]]
        assert nodes == ["schema_curator", "sql_generator", "executor", "insight_writer"]
