"""记忆层测试：词干判定（纯函数）/ 读写幂等与降级 / **偏好闭环**（最有价值的一条）。

偏好闭环 = 澄清一次 → 沉淀为长期记忆 → 下一轮不再打断且口径沿用。
它同时证明三件事：
  1. 记忆真的跨轮生效（否则第 2 轮还会追问）；
  2. 记忆不污染新会话（评测集每题独立 session，行为与改动前完全一致）；
  3. 用户当场说出的口径优先于历史偏好（记忆不能覆盖人的显式表述）。
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.supervisor import build_graph
from app.infra.memory import (
    choice_stem,
    explicitly_stated,
    forget_preferences,
    load_preferences,
    mark_preference_used,
    memory_stats,
    remember_preference,
)

pytestmark = pytest.mark.usefixtures("db_pools")

# mock curator 对含"销售额"的问题必报歧义（llm_mock._curator），用它做确定性触发器
QUESTION = "上个月各渠道的销售额是多少"


def _graph():
    return build_graph(checkpointer=InMemorySaver())


async def _pending_interrupt(graph, config) -> dict | None:
    state = await graph.aget_state(config)
    for task in state.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


class TestChoiceStem:
    """纯函数：口径词干与"本轮是否已显式说明口径"。"""

    def test_stem_from_full_option_and_short_answer(self) -> None:
        assert choice_stem("实付销售额") == "实付"
        assert choice_stem("实付销售额（支付成功订单的实付金额）") == "实付"
        assert choice_stem("GMV（成交总额，含取消/退款单）") == "gmv"
        assert choice_stem("  GMV  ") == "gmv"
        assert choice_stem("") == ""
        assert choice_stem("（只有括号）") == ""

    def test_explicitly_stated(self) -> None:
        assert explicitly_stated("各渠道实付销售额", "实付销售额") is True
        assert explicitly_stated("各渠道 GMV 是多少", "GMV（成交总额）") is True
        assert explicitly_stated("各渠道的销售额是多少", "实付销售额") is False
        assert explicitly_stated("各渠道的销售额", "") is False


class TestMemoryStore:
    """读写 / 幂等 upsert / 空输入 no-op（降级路径由 conftest 无表环境覆盖）。"""

    async def test_remember_load_upsert_and_stats(self) -> None:
        sid = "mem-loop"
        assert await remember_preference(sid, "销售额", "实付销售额") is True
        assert await load_preferences(sid) == {"销售额": "实付销售额"}

        await mark_preference_used(sid, "销售额")
        # 同 term 覆盖：choice 更新，hit_count 保留（记忆"被用过几次"是可观测指标）
        assert await remember_preference(sid, "销售额", "GMV") is True
        assert await load_preferences(sid) == {"销售额": "GMV"}
        stats = await memory_stats(sid)
        assert stats["terms"] == 1
        assert stats["hits"] == 1

        assert await forget_preferences(sid) == 1
        assert await load_preferences(sid) == {}

    async def test_empty_inputs_are_noop(self) -> None:
        assert await remember_preference("", "t", "c") is False
        assert await remember_preference("s", "", "c") is False
        assert await load_preferences("") == {}
        assert await forget_preferences("never-existed") == 0


class TestPreferenceLoop:
    """端到端闭环：这是"记忆真的存在"的证据。"""

    async def test_second_turn_reuses_preference_without_interrupt(self) -> None:
        graph = _graph()
        sid = "mem-hit"
        cfg = {"configurable": {"thread_id": "t-mem-hit"}}

        # 第 1 轮：没有任何记忆 → 必须澄清
        await graph.ainvoke(
            {"question": QUESTION, "session_id": sid, "status": "running"}, cfg
        )
        payload = await _pending_interrupt(graph, cfg)
        assert payload is not None and payload["type"] == "clarification"

        # 用户回答 → 恢复 → 完成；回答同时被沉淀为长期记忆（写路径）
        final = await graph.ainvoke(Command(resume="实付销售额"), cfg)
        assert final["status"] == "done"
        assert await load_preferences(sid) == {"销售额": "实付销售额"}

        # 第 2 轮：新 thread、同一 session → 命中记忆，不再打断用户
        cfg2 = {"configurable": {"thread_id": "t-mem-hit-2"}}
        await graph.ainvoke(
            {"question": QUESTION, "session_id": sid, "status": "running"}, cfg2
        )
        assert await _pending_interrupt(graph, cfg2) is None, "命中记忆后不应再追问"

        state = await graph.aget_state(cfg2)
        assert state.values["clarified_answer"] == "实付销售额", "口径应沿用记忆"
        applied = [
            step for step in state.values["trace"]
            if (step.get("detail") or {}).get("preference_applied")
        ]
        assert applied, "trace 应带 preference_applied 标记（前端时间线据此展示）"
        assert (await memory_stats(sid))["hits"] == 1, "命中次数应累加"

    async def test_fresh_session_still_clarifies(self) -> None:
        """回归：记忆不得污染新会话——评测集每题一个独立 session，行为必须不变。"""
        graph = _graph()
        cfg = {"configurable": {"thread_id": "t-mem-fresh"}}
        await graph.ainvoke(
            {"question": QUESTION, "session_id": "mem-fresh", "status": "running"}, cfg
        )
        payload = await _pending_interrupt(graph, cfg)
        assert payload is not None and payload["type"] == "clarification"

    async def test_explicit_caliber_in_question_beats_memory(self) -> None:
        """用户本轮自己说出口径 → 不被历史偏好吞掉（仍走既有澄清路径）。"""
        graph = _graph()
        sid = "mem-explicit"
        assert await remember_preference(sid, "销售额", "GMV（成交总额，含取消/退款单）") is True
        cfg = {"configurable": {"thread_id": "t-mem-explicit"}}
        await graph.ainvoke(
            {"question": "上个月各渠道实付销售额是多少", "session_id": sid, "status": "running"},
            cfg,
        )
        payload = await _pending_interrupt(graph, cfg)
        assert payload is not None and payload["type"] == "clarification"
