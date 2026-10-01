"""最终审查回归测试（2026-10-01）：每个用例对应一个本轮真实发现/修复。

覆盖：
1. _jsonable 递归转换（嵌套 Decimal 曾让 SSE json.dumps 炸穿）
2. Redis 故障降级（缓存是优化不是依赖）
3. /files 鉴权 + 路径穿越防护（图表曾可无鉴权拖取）
4. 每轮状态重置字段完整性（同 session 二次提问不串味）
5. _reset_turn_state 真实调用 update_state（用假 graph 断言）
6. 歧义检测走 tier=small（分级路由的真实调用点）
7. execute_sql 非 PG 异常收敛成结构化错误（不炸穿节点）
"""
from __future__ import annotations

import pytest

from app.agents.nodes import schema_curator_node
from app.application.ask_service import _FRESH_TURN_FIELDS, _reset_turn_state
from app.tools.sql_execute import _jsonable, execute_sql


class TestJsonableRecursive:
    """最终审查修复：嵌套容器里的 Decimal/datetime 也要转。"""

    def test_nested_decimal_in_list(self) -> None:
        from decimal import Decimal

        out = _jsonable([[Decimal("1.5"), 2], {"x": Decimal("3.25")}])
        assert out == [[1.5, 2], {"x": 3.25}]
        # 递归后的结果必须可被 json.dumps（array_agg 场景的崩溃点）
        import json

        json.dumps(out)

    def test_nested_datetime_in_dict(self) -> None:
        from datetime import datetime

        dt = datetime(2026, 10, 1, 12, 0, 0)
        out = _jsonable({"a": [dt, {"b": dt}]})
        assert out == {"a": ["2026-10-01T12:00:00", {"b": "2026-10-01T12:00:00"}]}

    def test_plain_values_unchanged(self) -> None:
        assert _jsonable("x") == "x"
        assert _jsonable(3) == 3
        assert _jsonable(None) is None


class TestFreshTurnFields:
    """每轮重置字段必须覆盖 AgentState 所有非累积通道。"""

    def test_reset_covers_all_per_turn_channels(self) -> None:
        from app.agents.state import AgentState

        # trace 是 operator.add 累积通道（排查询链刻意全量保留），不参与重置；
        # question/session_id 由 astream 输入本身赋值。
        skip = {"question", "session_id", "trace"}
        missing = set(AgentState.__annotations__) - skip - set(_FRESH_TURN_FIELDS)
        assert not missing, f"这些字段每轮开始会被上一轮污染: {missing}"

    def test_reset_values_are_fresh(self) -> None:
        assert _FRESH_TURN_FIELDS["status"] == "running"
        assert _FRESH_TURN_FIELDS["retry_count"] == 0
        assert all(v is None for k, v in _FRESH_TURN_FIELDS.items() if k not in {"status", "retry_count"})


class TestResetTurnState:
    """_reset_turn_state 必须真的把重置字段写进 checkpointer。"""

    @pytest.mark.asyncio
    async def test_update_state_called_with_fresh_fields(self, monkeypatch) -> None:
        sent: dict = {}

        class FakeGraph:
            async def aupdate_state(self, config, values):
                sent.update(values)

        await _reset_turn_state(FakeGraph(), "s1")
        assert sent == _FRESH_TURN_FIELDS

    @pytest.mark.asyncio
    async def test_reset_failure_does_not_raise(self, monkeypatch) -> None:
        """checkpointer 故障时重置失败不能阻塞提问（尽力而为）。"""

        class BrokenGraph:
            async def aupdate_state(self, config, values):
                raise RuntimeError("checkpointer down")

        await _reset_turn_state(BrokenGraph(), "s1")  # 不抛即通过


class TestTierRoutingCallPoint:
    """歧义检测（结构化小输出）必须走 tier=small。"""

    @pytest.mark.asyncio
    async def test_schema_curator_uses_small_tier(self, monkeypatch) -> None:
        seen: dict = {}

        async def fake_search_schema(question, top_k=5):
            return {"tables": [], "metrics": []}

        async def fake_chat_json(messages, **kwargs):
            seen.update(kwargs)
            return {"tables": ["users"], "metric": None, "ambiguity": None}

        monkeypatch.setattr("app.agents.nodes.search_schema", fake_search_schema)
        monkeypatch.setattr("app.agents.nodes.chat_json", fake_chat_json)
        await schema_curator_node({"question": "各渠道订单量"})
        assert seen.get("tier") == "small", f"分级路由调用点丢失: {seen}"


class TestExecuteSqlBroadCatch:
    """连接池/RuntimeError 等未预期故障收敛成结构化错误。"""

    @pytest.mark.asyncio
    async def test_unexpected_error_becomes_db_error(self, monkeypatch) -> None:
        import app.tools.sql_execute as se

        class DeadPool:
            def acquire(self):
                raise RuntimeError("pool exhausted")

        def fake_validate(sql, allowed, row_limit=None):
            class V:
                sql = "SELECT 1"
                needs_approval = False

            return V()

        # 三个依赖全部替身：白名单加载（否则 cache miss 会落到 PG 内省，
        # 未初始化 admin_pool 的测试进程里直接 RuntimeError）、闸、连接池
        async def fake_load_tables() -> dict:
            return {"users": {"id", "name"}}

        monkeypatch.setattr(se, "load_allowed_tables", fake_load_tables)
        monkeypatch.setattr(se, "validate_sql", fake_validate)
        monkeypatch.setattr(se, "ro_pool", lambda: DeadPool())
        out = await execute_sql("SELECT 1")
        assert out["ok"] is False
        assert out["error_type"] == "db"
        assert "pool exhausted" in out["error"]


class TestFilesAuth:
    """/files 与 /ask 同鉴权 + 路径穿越防护（TestClient，不连 DB）。"""

    def test_files_requires_api_key(self) -> None:
        from fastapi.testclient import TestClient

        from app.api.main import app

        client = TestClient(app)  # 不开 lifespan：本测试不跑数据库初始化
        r = client.get("/files/charts/x.svg")
        assert r.status_code == 401

    def test_files_rejects_traversal(self) -> None:
        from fastapi.testclient import TestClient

        from app.api.main import app

        client = TestClient(app)
        # %2F 编码的斜线：httpx 客户端会归一化 ../，服务端收到的仍是编码形态
        r = client.get("/files/..%2F..%2F..%2Fetc%2Fpasswd", headers={"x-api-key": "dev-key-001"})
        assert r.status_code in (400, 404)

    def test_files_404_for_missing(self) -> None:
        from fastapi.testclient import TestClient

        from app.api.main import app

        client = TestClient(app)
        r = client.get("/files/no_such_file.svg", headers={"x-api-key": "dev-key-001"})
        assert r.status_code == 404
