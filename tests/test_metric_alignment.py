"""口径对齐不变量：**注册表定义 == 评测集金标算式**。

为什么需要（真实事故）：`退款率` 的注册表定义是"退款单 / **支付成功单**"，而金标 SQL 用的是
`COUNT(*)`（**全部订单**）——模型严格照注册表作答反被判错。`整体退款率` 与 `退款率最高的渠道`
两题在历史评测报告里**从未通过过**（2026-09-29 起每份报告都失败）。根因不是模型能力，
而是**评测与口径两套事实源发生了漂移**。

这个测试把"漂移"从**静默错误**变成**红灯**：以后改了注册表却没同步金标（或反之），
这里立刻失败——与 `test_eval_hash`（金标哈希不变量）同一个思路，是评测体系的信任根。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.infra.db import admin_pool

pytestmark = pytest.mark.usefixtures("db_pools")


@dataclass(frozen=True)
class Alignment:
    """一个"注册表口径 ↔ 金标问题"的对齐用例。"""

    metric: str          # biz.metric_definitions.metric_name
    question: str        # eval.queries.question
    scale: float = 1.0   # 注册表算比率(0.055)，金标算百分比(5.55) 时填 100
    dimensioned: bool = False  # 金标是按维度分组的多行结果（如各品类、各渠道）


# 只收录"sql_hint 可直接执行（无 $1/$2 占位）"的口径——这样比较的是真实算式而不是复述
CASES = [
    # 退款率：注册表与金标都按"百分比、两位小数"约定（scale=1）——单位约定见下方不变量测试
    Alignment(metric="退款率", question="整体退款率"),
    Alignment(metric="客单价", question="整体客单价"),
    Alignment(metric="连带率", question="各商品品类的连带率（平均每单件数）", dimensioned=True),
]


async def _registry_hint(conn, metric: str) -> str:
    hint = await conn.fetchval(
        "SELECT sql_hint FROM biz.metric_definitions WHERE metric_name = $1", metric
    )
    assert hint, f"口径 {metric} 未注册（biz.metric_definitions 缺这一条）"
    return hint


async def _gold(conn, question: str) -> str:
    sql = await conn.fetchval("SELECT gold_sql FROM eval.queries WHERE question = $1", question)
    assert sql, f"金标缺失：{question}（先跑 eval/build_eval_set.py）"
    return sql


async def test_paramless_hints_are_executable() -> None:
    """注册表健康检查：无占位符的口径 SQL 必须能跑通（防手写口径语法错误）。"""
    async with admin_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT metric_name, sql_hint FROM biz.metric_definitions ORDER BY metric_name"
        )
        checked_metrics: set[str] = set()
        for r in rows:
            if "$1" in r["sql_hint"] or "$2" in r["sql_hint"]:
                continue  # 带时间参数的模板不在本测试范围（无法独立执行）
            try:
                await conn.execute(r["sql_hint"])
            except Exception as e:  # noqa: BLE001
                pytest.fail(f"口径 {r['metric_name']} 的 sql_hint 执行失败：{e}")
            checked_metrics.add(r["metric_name"])
        assert checked_metrics, "注册表里没有可直接执行的口径——检查 sql_hint"
        # 对齐用例依赖的口径必须可直接执行，否则比较就退化成"复述算式"
        for case in CASES:
            assert case.metric in checked_metrics, (
                f"{case.metric} 的 sql_hint 带占位符，无法与金标做数值比较——"
                f"本测试要求它的口径可直接执行"
            )


@pytest.mark.parametrize("case", [c for c in CASES if not c.dimensioned], ids=lambda c: c.metric)
async def test_registry_scalar_matches_gold(case: Alignment) -> None:
    """标量口径：注册表算式的结果 == 金标结果（同一指标两处定义必须给同一数字）。"""
    async with admin_pool().acquire() as conn:
        registry_value = float(await conn.fetchval(await _registry_hint(conn, case.metric)))
        gold_value = float(await conn.fetchval(await _gold(conn, case.question)))
    assert round(registry_value * case.scale, 2) == round(gold_value, 2), (
        f"口径漂移：{case.metric} 注册表算出 {round(registry_value * case.scale, 2)}，"
        f"金标（{case.question}）算出 {round(gold_value, 2)}——两者必须一致，"
        f"否则模型照注册表作答会被判错（这正是退款率事故的成因）"
    )


@pytest.mark.parametrize("case", [c for c in CASES if c.dimensioned], ids=lambda c: c.metric)
async def test_registry_dimensioned_matches_gold(case: Alignment) -> None:
    """分维度口径：逐行比较（注册表 hint 自带 GROUP BY，与金标同为多行结果）。"""
    async with admin_pool().acquire() as conn:
        registry_rows = await conn.fetch(await _registry_hint(conn, case.metric))
        gold_rows = await conn.fetch(await _gold(conn, case.question))

    def norm(rows):
        return sorted((str(r[0]), round(float(r[1]) * case.scale, 2)) for r in rows)

    assert norm(registry_rows) == norm(gold_rows), (
        f"口径漂移：{case.metric} 注册表结果与金标（{case.question}）不一致\n"
        f"  注册表: {norm(registry_rows)[:3]}\n  金标: {norm(gold_rows)[:3]}"
    )


# 形态受控词表（L4 生成层按形态选 SQL 写法，见 prompts 的"形态 → SQL 写法"）
USAGE_FORMS = ("整体类", "分组类", "占比类", "排名类", "人均类", "转化类")

# 比率类口径：必须统一以百分比表示（表示形式也是约定的一部分）
RATIO_METRICS = ("复购率", "退款率", "下单转化率", "下单到支付转化率")


async def test_every_metric_declares_usage_form() -> None:
    """**形态词表不变量**：每条口径都要标注形态，且首段来自受控词表。

    为什么必须守：L4 是按形态选 SQL 写法的（占比类要用标量子查询做分母、排名类要排序取
    TopN、人均类要先聚合再平均）。形态缺失或写错，模型只能猜——"占比类返回绝对值"
    "人均类算成订单数"这类失败都源于此。新增口径忘了写形态，这条测试立刻红灯。
    """
    async with admin_pool().acquire() as conn:
        rows = await conn.fetch("SELECT metric_name, usage_hint FROM biz.metric_definitions")
    assert rows, "口径注册表为空——先跑 scripts/sync_metric_definitions.py"
    missing = [r["metric_name"] for r in rows if not (r["usage_hint"] or "").strip()]
    assert not missing, f"以下口径没有标注形态：{missing}"
    for r in rows:
        head = (r["usage_hint"] or "").split("：")[0].split("/")[0].strip()
        assert head in USAGE_FORMS, (
            f"{r['metric_name']} 的形态 '{head}' 不在受控词表 {USAGE_FORMS}"
        )


async def test_ratio_metrics_use_percentage_convention() -> None:
    """**单位约定不变量**：比率类口径一律以百分比表示（`ROUND(100.0 * ..., 2)`）。

    事故记录：初版 hint 返回**比率**（0.056）而评测金标返回**百分比**（5.60）——模型逐字
    照抄注册表反被判错（**口径完全正确，只是单位不同**）。结果集哈希比的是原始值，
    所以"表示形式"必须在两处保持一致，且必须有测试守住。
    """
    async with admin_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT metric_name, sql_hint FROM biz.metric_definitions "
            "WHERE metric_name = ANY($1::text[])", list(RATIO_METRICS),
        )
    found = {r["metric_name"] for r in rows}
    assert found == set(RATIO_METRICS), f"注册表缺少比率类口径：{set(RATIO_METRICS) - found}"
    for r in rows:
        assert "100.0 *" in r["sql_hint"], (
            f"{r['metric_name']} 的 sql_hint 未遵守百分比约定（应含 ROUND(100.0 * ..., 2)）："
            f"{r['sql_hint'][:90]}"
        )


# 题面里出现这些词 = 有分组/排名意图（尽力而为：语义判断不可靠，所以还有人工审查清单兜底）
_SHAPE_HINTS = ("各", "按", "分", "排名", "top", "前", "每", "对比")

# **人工审查清单**：金标最外层 GROUP BY、但题面看不出分组意图的用例。
# 这些已逐条确认"题面虽无分组词，但按维度给出结果才是自然答案"（如"…对比""…最高的小时"）。
# 新增条目 → 测试红灯，必须人工判断是"题面/判据不一致"还是"确认无误后加进本清单"。
REVIEWED_SHAPE_IDS: set[int] = {
    40,  # 下单金额最高的 10 个用户 —— "最高的 10 个用户"本身就隐含按用户聚合 ✓
    74,  # 双 11 当天订单量最高的小时 —— "最高的小时"隐含按小时聚合 ✓
}


def _outer_shape(sql: str) -> tuple[bool, bool]:
    """(最外层是否有 GROUP BY, 最外层是否有 LIMIT)——用 sqlglot 解析，别用字符串匹配。

    为什么必须解析：大量金标在**子查询**里 GROUP BY（如"复购用户数"= SELECT COUNT(*)
    FROM (SELECT user_id ... GROUP BY user_id) t）——那是聚合手段，不是结果维度，
    用 "GROUP BY" in sql 判断会误报一片。
    """
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, read="postgres")
    outer = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if outer is None:
        return False, False
    return outer.args.get("group") is not None, outer.args.get("limit") is not None


async def test_gold_shape_matches_question() -> None:
    """**L6 判据形状不变量**：金标 SQL 的形状必须与题面语义一致。

    真实事故：题面"退款率最高的渠道"（自然答案是 Top-1），而金标返回**全部渠道的排名**——
    模型答对了（app 5.7%）却被判错；同类的"unpaid 订单占比"题面问整体、金标却按渠道分组。
    这类"**答对但判错**"最伤士气、也最难发现（表面看只是"模型不稳定"）。

    规则（用 sqlglot 只看**最外层**形状——子查询内部的 GROUP BY 是聚合手段，不算结果维度）：
      题面没有分组意图（各/按/分/排名/Top/前），而**最外层** SELECT 却 GROUP BY → 判据形状与题面不符
    """
    # 只查执行类：行为类（ambiguous/should_refuse/unanswerable）的 gold_sql 是**哨兵注释**
    # （如"-- 歧义：注册用户 vs 下单用户，正确行为是追问"），它们没有 SQL 形状可言
    async with admin_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, question, gold_sql FROM eval.queries "
            "WHERE category IN ('simple_agg','multi_join','time_range','metric_def') "
            "ORDER BY id"
        )
    assert rows, "eval.queries 为空——先跑 eval/build_eval_set.py"
    problems: list[str] = []
    for r in rows:
        q = r["question"].lower()
        if any(h in q for h in _SHAPE_HINTS):
            continue  # 题面本身就有分组/排名意图，形状由题面决定
        outer_group_by, _limit = _outer_shape(r["gold_sql"] or "")
        if outer_group_by and r["id"] not in REVIEWED_SHAPE_IDS:
            problems.append(f"[{r['id']}] {r['question']}")
    assert not problems, (
        "以下用例的金标最外层 GROUP BY、但题面看不出分组意图——**必须逐条人工确认**：\n  "
        + "\n  ".join(problems)
        + "\n\n处理方式：①若题面确实没问这个维度（如'退款率最高的渠道'却给全渠道排名）→ 改题面或金标；"
        "②若确认无误（如'…对比''…最高的小时'）→ 把 id 加进 REVIEWED_SHAPE_IDS。"
    )


async def test_refund_rate_denominator_is_paid_orders() -> None:
    """**回归锁定**：退款率的分母必须是"支付成功单"，不是全部订单。

    这是那个历史事故的靶心——如果谁把分母改回 COUNT(*)，这条测试立刻红灯。
    """
    async with admin_pool().acquire() as conn:
        hint = await _registry_hint(conn, "退款率")
        gold_sql = await _gold(conn, "整体退款率")
        gold = float(await conn.fetchval(gold_sql))   # 注意：要执行，不是拿 SQL 文本比
        paid_only = float(await conn.fetchval(
            "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status='refunded') "
            "/ NULLIF(COUNT(*) FILTER (WHERE order_status IN ('paid','refunded')),0), 2) "
            "FROM biz.orders"
        ))
        all_orders = float(await conn.fetchval(
            "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status='refunded') "
            "/ COUNT(*), 2) FROM biz.orders"
        ))
    assert paid_only != all_orders, "两种分母应给出不同数值（否则本测试失去意义）"
    assert "('paid','refunded')" in hint, "注册表退款率的分母应限定为支付成功单"
    assert "('paid','refunded')" in gold_sql, "金标退款率的分母也必须限定为支付成功单"
    assert abs(gold - paid_only) < 0.01, (
        f"金标退款率 {gold} 应等于「支付成功单为分母」的 {paid_only}，而不是「全部订单」的 {all_orders}"
    )
