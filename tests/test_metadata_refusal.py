"""元数据类问题（表结构/列数/索引）拒答测试。

真实模型实测事故：问"流量日志表一共有多少列记录"，模型生成
`SELECT COUNT(*) FROM biz.traffic_logs WHERE id IS NOT NULL` 并回答
"共 20000 条记录"——**把"列"当"行"**，比拒答更糟（用户会当真）。
而正确答案要查 information_schema，被闸 4 表白名单禁止——所以正确结局是拒答。
"""
from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.nodes import is_metadata_question
from app.agents.supervisor import build_graph
from app.infra.db import admin_pool

pytestmark = pytest.mark.usefixtures("db_pools")


def _graph():
    return build_graph(checkpointer=InMemorySaver())


class TestMetadataDetection:
    """规则判定（纯函数）：命中的要拒答，业务问题一律不能误伤。"""

    @pytest.mark.parametrize("q", [
        "流量日志表一共有多少列记录",
        "orders 表有几列",
        "这个表有哪些列",
        "商品表的字段名是什么",
        "orders 的表结构是什么",
        "给我建表语句",
        "这张表有哪些索引",
        "主键是什么",
    ])
    def test_metadata_questions_detected(self, q: str) -> None:
        assert is_metadata_question(q) is True, q

    @pytest.mark.parametrize("q", [
        "上个月各渠道的实付销售额是多少",
        "流量日志表一共有多少行记录",
        "各城市的订单量排名",
        "昨天有多少订单",
        "最近哪个商品卖得最好",
        "哪个渠道的客单价最高",
    ])
    def test_business_questions_not_detected(self, q: str) -> None:
        assert is_metadata_question(q) is False, q

    async def test_never_hijacks_guard_cases(self) -> None:
        """**不变量**：元数据规则不得吞掉评测集里既有的越权/歧义用例。

        should_refuse 类（如"数据库里都有哪些表""给我所有订单的全部字段"）必须继续
        走七道闸拦截——如果被元数据规则提前拒答，它们的判定就从"被闸拦截"变成
        "普通拒答"，评测会掉分，安全叙事也会失真。
        """
        async with admin_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT question, category FROM eval.queries "
                "WHERE category IN ('should_refuse', 'ambiguous')"
            )
        assert rows, "eval.queries 为空——先跑 eval/build_eval_set.py"
        hijacked = [r["question"] for r in rows if is_metadata_question(r["question"])]
        assert not hijacked, f"元数据规则吞掉了评测用例：{hijacked}"


class TestMetadataRefusalFlow:
    """状态机：拒答是**终态**，不能继续生成 SQL。"""

    async def test_metadata_question_refuses_without_sql(self) -> None:
        graph = _graph()
        cfg = {"configurable": {"thread_id": "t-meta-refuse"}}
        final = await graph.ainvoke(
            {"question": "流量日志表一共有多少列记录", "session_id": "meta-1", "status": "running"},
            cfg,
        )
        assert final["status"] == "done", "拒答是正常终态，不是 failed"
        assert final.get("sql") is None, "拒答绝不能生成 SQL（这次就是 COUNT(*) 答错了）"
        assert final.get("refusal"), "应写入 refusal 话术"
        assert "无法回答" in final["insight"]["summary"], "summary 走 result 事件给用户看"
        steps = [s for s in final["trace"] if s["node"] == "schema_curator"]
        assert steps and steps[-1]["detail"].get("refused") == "metadata"

    async def test_metadata_refusal_skips_llm_and_db(self) -> None:
        """拒答不调 LLM（省钱）、不生成 SQL：trace 里只有一个节点。"""
        graph = _graph()
        cfg = {"configurable": {"thread_id": "t-meta-fast"}}
        final = await graph.ainvoke(
            {"question": "orders 表有哪些索引", "session_id": "meta-2", "status": "running"}, cfg
        )
        nodes = [s["node"] for s in final["trace"]]
        assert nodes == ["schema_curator"], f"应止步于 curator，实际经过 {nodes}"

    async def test_normal_question_still_generates_sql(self) -> None:
        """回归：普通业务问题不受影响，照常走完生成+执行。"""
        graph = _graph()
        cfg = {"configurable": {"thread_id": "t-meta-normal"}}
        final = await graph.ainvoke(
            {"question": "上个月有多少下单用户", "session_id": "meta-3", "status": "running"}, cfg
        )
        assert final.get("refusal") is None
        assert final.get("sql"), "普通问题必须照常生成 SQL"
        assert final["status"] == "done"
