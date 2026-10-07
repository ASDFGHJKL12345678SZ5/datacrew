"""Schema 路由测试：维度值索引 + 小 schema 全量注入（D2 两个静默故障的回归）。

背景（真实模型实测事故）：问"北京销售额多少"，检索只召回 orders/traffic_logs/
order_items——**users 表没进上下文**，模型看不到 city 列，于是"北京"和"上海"生成
逐字节相同的 SQL（都是全国汇总）。根因不是模型能力，是**上下文缺失**。
"""
from __future__ import annotations

import pytest

from app.tools.schema_registry import load_allowed_tables
from app.tools.schema_search import (
    FULL_SCHEMA_MAX_TABLES,
    load_dimension_index,
    match_dimension_values,
    search_schema,
)

pytestmark = pytest.mark.usefixtures("db_pools")


class TestMatchDimensionValues:
    """纯函数：问题里命中了哪些已知维度取值。"""

    def test_hits_known_value(self) -> None:
        index = {"北京": [["users", "city"]], "app": [["orders", "channel"]]}
        assert match_dimension_values("北京销售额多少", index) == {"北京": [["users", "city"]]}
        assert match_dimension_values("app 渠道的订单量", index) == {"app": [["orders", "channel"]]}
        assert match_dimension_values("各渠道销售额", index) == {}


class TestDimensionIndex:
    """值索引构建：只索引低基数 text 列（高基数列必须排除）。"""

    async def test_city_is_indexed_and_high_cardinality_is_not(self) -> None:
        index = await load_dimension_index()
        assert index, "值索引为空——检查 PG 是否可连、biz 表是否有 text 列"
        # 城市是低基数维度：北京必须能映射到 users.city
        assert ["users", "city"] in index.get("北京", []), f"北京 未映射到 users.city：{index.get('北京')}"
        # 高基数（用户姓名 5000 个）不该进索引，否则噪声淹没信号
        assert "users" not in [t for pairs in index.values() for t, c in pairs if c == "name"]
        # 渠道值也在
        assert ["orders", "channel"] in index.get("app", [])

    async def test_index_degrades_to_empty_dict_on_failure(self, monkeypatch) -> None:
        """降级：值索引是增强不是依赖，构建失败返回空词典而不是抛错。"""
        from app.tools import schema_search as mod

        async def boom():
            raise RuntimeError("pg down")

        monkeypatch.setattr(mod, "_build_dimension_index", boom)
        assert await mod.load_dimension_index(use_cache=False) == {}


class TestSearchSchemaCoverage:
    """路由覆盖：城市问题必须带上 users；小 schema 必须全量注入。"""

    async def test_city_question_includes_users(self) -> None:
        ctx = await search_schema("北京销售额多少", top_k=5)
        names = [t["name"] for t in ctx["tables"]]
        assert "users" in names, f"users 未召回：{names}"
        assert "北京" in ctx["matched_values"], "应记录命中的维度取值（可观测）"
        users = next(t for t in ctx["tables"] if t["name"] == "users")
        assert "city" in users["columns"], "上下文里必须能看到 city 列"

    async def test_small_schema_is_fully_injected(self) -> None:
        allowed = await load_allowed_tables()
        assert len(allowed) <= FULL_SCHEMA_MAX_TABLES, "本测试假设 schema 规模小（表数少）"
        ctx = await search_schema("上个月各渠道的实付销售额是多少", top_k=5)
        names = {t["name"] for t in ctx["tables"]}
        assert names == set(allowed), f"小 schema 应全量注入，实际 {sorted(names)}"
        # 但排序仍然有意义：命中的表分更高（分数仅供模型参考）
        assert ctx["tables"][0]["score"] >= ctx["tables"][-1]["score"]

    async def test_metrics_still_returned_in_full(self) -> None:
        """口径候选集不可被过滤（歧义检测依赖两个口径同时在场）——回归保护。"""
        ctx = await search_schema("各渠道的销售额", top_k=5)
        assert len(ctx["metrics"]) >= 2, "口径全量返回被破坏了"
