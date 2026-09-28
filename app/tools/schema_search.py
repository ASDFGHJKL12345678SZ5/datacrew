"""Schema 检索工具：Agent 的"眼睛"——问数前先找到相关表和指标口径。

D1 实现（确定性算法，可解释）：
    1. 指标口径：**全量返回**。关键设计决策——歧义检测的候选集不能被预先过滤：
       "销售额"的歧义需要 GMV 和实付销售额两个口径同时在场，关键词过滤会把
       歧义的另一半滤掉，LLM 就看不到歧义了。口径注册表只有十几条，全量注入
       成本可忽略（表清单才需要按需过滤）。
    2. 表路由：从指标的 sql_hint 里正则提取 biz.xxx 表名 —— 口径注册表同时承担
       "表路由"职责，因为每个指标本身就说明了它查哪些表
    3. 兜底：无命中时返回全量表（schema 只有 6 张）

D2 升级路径：口径量增长后（>100 条）再上 pgvector 语义检索做表/口径预过滤，
但歧义候选集仍要保证覆盖 —— 检索可以排序，不能把候选滤空。
"""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.tools.schema_registry import load_allowed_tables, load_metric_definitions

log = get_logger(__name__)

_TABLE_RE = re.compile(r"(?:FROM|JOIN)\s+biz\.(\w+)", re.IGNORECASE)


def _ngram_score(term: str, text: str) -> int:
    """2-gram 重叠度：term 的连续二字片段在 text 中出现的次数。

    例：term="实付销售额", text="上个月各渠道的销售额" -> "销售"/"售额" 命中 -> 2 分
    整词命中加权 10 分（精确匹配优先）。
    """
    if not term:
        return 0
    if term in text:
        return 10
    if len(term) < 2:
        return 0
    return sum(1 for i in range(len(term) - 1) if term[i : i + 2] in text)


async def search_schema(question: str, top_k: int = 5) -> dict:
    """按问题检索相关表与指标口径。

    Returns:
        {"tables": [{"name", "columns", "score"}], "metrics": [{...}]}
    """
    allowed = await load_allowed_tables()
    metrics = await load_metric_definitions()

    # 1. 指标：全量返回（歧义候选集不可预过滤，见模块 docstring），2-gram 仅用于排序
    scored_metrics = [
        (_ngram_score(m["metric_name"], question), m) for m in metrics
    ]
    scored_metrics.sort(key=lambda x: -x[0])
    matched = [m for _, m in scored_metrics]

    # 2. 表路由：按指标与问题的相关度排序，高相关指标的 sql_hint 提取表名
    routed: dict[str, int] = {}
    for score, m in scored_metrics:
        weight = score if score > 0 else 1  # 未命中的指标也参与路由（权重低）
        for t in _TABLE_RE.findall(m.get("sql_hint") or ""):
            if t in allowed:
                routed[t] = routed.get(t, 0) + weight

    # 3. 兜底：无命中指标时按问题与表/列名的 2-gram 匹配，再兜底全量
    if not routed:
        for table, columns in allowed.items():
            score = _ngram_score(table, question) + sum(
                _ngram_score(c, question) for c in columns
            )
            if score > 0:
                routed[table] = score
    tables = [
        {"name": t, "columns": sorted(allowed[t]), "score": s}
        for t, s in sorted(routed.items(), key=lambda x: -x[1])
    ]
    if not tables:
        tables = [
            {"name": t, "columns": sorted(c), "score": 0}
            for t, c in list(allowed.items())[:top_k]
        ]

    log.info(
        "schema.search",
        extra={"context": {"tables": [t["name"] for t in tables], "metrics": len(matched)}},
    )
    return {"tables": tables, "metrics": matched}
