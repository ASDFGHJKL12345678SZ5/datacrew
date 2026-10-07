"""Schema 检索工具：Agent 的"眼睛"——问数前先找到相关表和指标口径。

D1 实现（确定性算法，可解释）：
    1. 指标口径：**全量返回**。关键设计决策——歧义检测的候选集不能被预先过滤：
       "销售额"的歧义需要 GMV 和实付销售额两个口径同时在场，关键词过滤会把
       歧义的另一半滤掉，LLM 就看不到歧义了。口径注册表只有十几条，全量注入
       成本可忽略（表清单才需要按需过滤）。
    2. 表路由：从指标的 sql_hint 里正则提取 biz.xxx 表名 —— 口径注册表同时承担
       "表路由"职责，因为每个指标本身就说明了它查哪些表
    3. 兜底：无命中时返回全量表（schema 只有 6 张）

D2 修复（真实模型实测事故，两个静默故障）：
    4. **维度值索引（value index）**：问题里的"北京"是**值**不是列名，指标 sql_hint
       里也没有 users 表 → users 永远进不了上下文 → 模型看不到 city 列 → "北京销售额"
       与"上海销售额"生成**逐字节相同**的 SQL（都是全国汇总），用户拿到一样的数字。
       **这不是模型能力问题，是上下文缺失**。做法：把低基数 text 列（distinct ≤ 50）
       的取值建成值→(表,列) 词典，问题里出现已知取值就把该表增补进路由。
    5. **小 schema 全量注入**：表数 ≤ FULL_SCHEMA_MAX_TABLES 时不做过滤。过滤的价值在
       大 schema（几十上百张表），而"漏表"是**静默故障**——模型看不到列就永远答错且不报错。
       当前 6 张表全量注入只多几十 token，收益是消灭整类缺陷。

D3 升级路径：口径量增长后（>100 条）再上 pgvector 语义检索做表/口径预过滤，
但歧义候选集仍要保证覆盖 —— 检索可以排序，不能把候选滤空。
"""
from __future__ import annotations

import re
import time

from app.core.logging import get_logger
from app.infra.cache import cache_get, cache_set
from app.tools.schema_registry import load_allowed_tables, load_metric_definitions

log = get_logger(__name__)

_TABLE_RE = re.compile(r"(?:FROM|JOIN)\s+biz\.(\w+)", re.IGNORECASE)

# 表数不超过这个值时全量注入（见模块 docstring 第 5 条）
FULL_SCHEMA_MAX_TABLES = 10

# 维度值索引：只索引 text/varchar 且 distinct ≤ 上限的列（低基数 = 维度）
_DIM_MAX_DISTINCT = 50
_DIM_INDEX_KEY = "datacrew:dim_index:v1"
_DIM_INDEX_TTL_S = 3600
_L1_TTL_S = 60.0
_TEXT_COLUMNS_SQL = """
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'biz'
  AND data_type IN ('character varying', 'text', 'character')
ORDER BY table_name, column_name
"""
# 标识符白名单：绝不把未校验的名字拼进 SQL（表/列名来自 information_schema，仍再校验一次）
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_l1_dim: tuple[dict[str, list[list[str]]], float] | None = None


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


async def _build_dimension_index() -> dict[str, list[list[str]]]:
    """扫描 biz 的 text 列，建 值 → [[表, 列], ...] 词典（跳过非低基数列）。"""
    from app.infra.db import admin_pool  # 局部导入：避免工具层与连接层循环依赖

    index: dict[str, list[list[str]]] = {}
    async with admin_pool().acquire() as conn:
        columns = await conn.fetch(_TEXT_COLUMNS_SQL)
        for row in columns:
            table, column = row["table_name"], row["column_name"]
            if not (_IDENT_RE.match(table) and _IDENT_RE.match(column)):
                continue
            values = await conn.fetch(
                'SELECT DISTINCT "' + column + '" AS v FROM biz."' + table + '"'
                ' WHERE "' + column + '" IS NOT NULL LIMIT ' + str(_DIM_MAX_DISTINCT + 1)
            )
            if len(values) > _DIM_MAX_DISTINCT:
                continue  # 高基数（如 users.name）：不是维度，索引它只会制造噪声
            for v in values:
                key = str(v["v"]).strip()
                if len(key) < 2:
                    continue
                pair = [table, column]
                bucket = index.setdefault(key, [])
                if pair not in bucket:
                    bucket.append(pair)
    return index


async def load_dimension_index(use_cache: bool = True) -> dict[str, list[list[str]]]:
    """值→(表,列) 词典（Redis 1h + 进程内 L1）。任何失败返回空词典（降级=不增补表）。"""
    global _l1_dim
    if use_cache and _l1_dim is not None and _l1_dim[1] > time.monotonic():
        return _l1_dim[0]
    if use_cache:
        cached = await cache_get(_DIM_INDEX_KEY)
        if cached:
            _l1_dim = (cached, time.monotonic() + _L1_TTL_S)
            return cached
    try:
        index = await _build_dimension_index()
    except Exception as e:  # 降级：值索引是增强，不是依赖
        log.warning("dim_index.build_failed", extra={"context": {"reason": str(e)[:120]}})
        return {}
    await cache_set(_DIM_INDEX_KEY, index, _DIM_INDEX_TTL_S)
    _l1_dim = (index, time.monotonic() + _L1_TTL_S)
    log.info("dim_index.built", extra={"context": {"values": len(index)}})
    return index


def match_dimension_values(
    question: str, index: dict[str, list[list[str]]]
) -> dict[str, list[list[str]]]:
    """问题里出现了哪些已知维度取值（纯函数，可单测）。"""
    hits: dict[str, list[list[str]]] = {}
    for value, pairs in index.items():
        if value in question:
            hits[value] = pairs
    return hits


async def search_schema(question: str, top_k: int = 5) -> dict:
    """按问题检索相关表与指标口径。

    Returns:
        {"tables": [{"name", "columns", "score"}], "metrics": [{...}],
         "matched_values": {取值: [[表, 列]]}}
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

    # 4. 维度值命中（D2 修复）：把"值"映射到它所在的表并增补进路由
    matched_values = match_dimension_values(question, await load_dimension_index())
    for pairs in matched_values.values():
        for table, _column in pairs:
            if table in allowed:
                routed[table] = routed.get(table, 0) + 10

    # 5. 小 schema 全量注入（D2 修复）：不移除任何表，只补全，消除"漏表"静默故障
    if len(allowed) <= FULL_SCHEMA_MAX_TABLES:
        for t in allowed:
            routed.setdefault(t, 0)

    ordered = sorted(routed.items(), key=lambda x: -x[1])
    # 小 schema：全量保留（只排序不截断）；大 schema：按分数取 top_k
    limit = len(ordered) if len(allowed) <= FULL_SCHEMA_MAX_TABLES else top_k
    tables = [
        {"name": t, "columns": sorted(allowed[t]), "score": s} for t, s in ordered[:limit]
    ]
    if not tables:
        tables = [
            {"name": t, "columns": sorted(c), "score": 0}
            for t, c in list(allowed.items())[:top_k]
        ]

    log.info(
        "schema.search",
        extra={"context": {
            "tables": [t["name"] for t in tables],
            "metrics": len(matched),
            "matched_values": list(matched_values),
        }},
    )
    return {"tables": tables, "metrics": matched, "matched_values": matched_values}
