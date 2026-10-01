"""chart_gen 单测：三种图 + 安全转义 + 错误输入（零依赖，无需 DB）。"""
from __future__ import annotations

import pytest

from app.agents.nodes import _chart_points
from app.tools.chart_gen import ChartError, generate_chart, render_svg

DATA = [["app", 25800], ["miniapp", 16400], ["h5", 4700]]


class TestRender:
    def test_bar(self) -> None:
        svg = render_svg("bar", DATA, "各渠道实付销售额")
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "<rect" in svg and "各渠道实付销售额" in svg

    def test_line(self) -> None:
        assert "<polyline" in render_svg("line", DATA, "趋势")

    def test_pie(self) -> None:
        svg = render_svg("pie", [["a", 3], ["b", 1]], "占比")
        assert "<path" in svg

    def test_dict_input(self) -> None:
        svg = render_svg("bar", [{"label": "x", "value": 1}], "t")
        assert "<rect" in svg

    def test_xml_escaping(self) -> None:
        # 标签来自查询结果（用户数据），必须实体转义防注入
        svg = render_svg("bar", [["<script>alert(1)</script>", 1]], "t")
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg

    def test_negative_values_bar(self) -> None:
        # 负值（如退货金额）也要能画：基线取 min(0, ...)
        svg = render_svg("bar", [["a", -5], ["b", 3]], "t")
        assert "<rect" in svg


class TestValidation:
    @pytest.mark.parametrize("bad,desc", [
        ([], "空列表"),
        ([["a", "not-number"]], "非数值"),
        ([[1, 2, 3]], "行过短"),
        ([["a", 1]] * 25, "超过 MAX_POINTS"),
        ("not-a-list", "非列表"),
    ])
    def test_bad_data_rejected(self, bad: object, desc: str) -> None:
        with pytest.raises(ChartError):
            render_svg("bar", bad)  # type: ignore[arg-type]

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ChartError, match="不支持的图表类型"):
            render_svg("radar", DATA)

    def test_pie_non_positive_total_rejected(self) -> None:
        with pytest.raises(ChartError):
            render_svg("pie", [["a", 0], ["b", 0]], "t")


class TestGenerateChart:
    @pytest.mark.asyncio
    async def test_generate_and_store(self) -> None:
        r = await generate_chart("bar", DATA, "测试")
        assert r["ok"] is True
        assert r["url"].startswith("/files/charts/")
        assert r["format"] == "svg"
        assert r["points"] == 3

    @pytest.mark.asyncio
    async def test_generate_bad_type(self) -> None:
        r = await generate_chart("radar", DATA)
        assert r["ok"] is False
        assert r["error_type"] == "bad_request"


class TestChartPointsFromResult:
    """InsightWriter 的取数逻辑：第一文本列做标签、无数值列则跳过。"""

    def test_basic(self) -> None:
        result = {
            "columns": ["channel", "pay_total"],
            "rows": [["app", 25800.0], ["h5", 4700.0]],
        }
        points = _chart_points(result)
        assert points == [["app", 25800.0], ["h5", 4700.0]]

    def test_numeric_first_column_skipped_as_label(self) -> None:
        result = {"columns": ["id", "amount"], "rows": [[1, 99.5], [2, 12.0]]}
        points = _chart_points(result)
        assert points == [["1", 99.5], ["2", 12.0]]

    def test_no_numeric_column_returns_none(self) -> None:
        result = {"columns": ["city", "name"], "rows": [["北京", "张三"]]}
        assert _chart_points(result) is None

    def test_single_column_returns_none(self) -> None:
        result = {"columns": ["cnt"], "rows": [[5]]}
        assert _chart_points(result) is None

    def test_ten_row_cap(self) -> None:
        result = {
            "columns": ["k", "v"],
            "rows": [[str(i), float(i)] for i in range(50)],
        }
        assert len(_chart_points(result)) == 10
