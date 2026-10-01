"""Schema 注册表：Agent 可见的表/列白名单（安全闸闸4/闸5 的数据源）。

加载链（三级缓存，最终审查后与 README 对齐的真实链路）：
    L1 进程内 TTL 缓存（30s）→ L2 Redis（TTL 1h）→ L3 PostgreSQL information_schema
为什么三级：schema 每条问数都要用（闸4/闸5）；L1 消掉 Redis 往返，L2 跨
进程/跨重启共享，L3 是真相源。每一级 miss 才落下一级，Redis 故障自动降级
到直查库（cache.py 的降级路径），缓存是优化不是依赖。
为什么从 DB 内省而不是手写配置：手写配置会随 DDL 漂移失效，内省永远与真实 schema 一致。
"""
from __future__ import annotations

import time

from app.core.logging import get_logger
from app.infra.cache import cache_get, cache_set, get_redis
from app.infra.db import admin_pool

log = get_logger(__name__)

CACHE_KEY = "schema:allowed_tables"
CACHE_TTL_S = 3600
L1_TTL_S = 30  # 进程内缓存短 TTL：要能及时看到 DDL 变更，又要挡掉单进程内的重复读

# L1：进程内 (数据, 过期时间戳)。多 worker 各自一份，TTL 短，牺牲几秒的
# 新鲜度换每条问数一次 Redis 往返——评测实测 schema 加载是 curator 的固定开销。
_l1_allowed: tuple[dict[str, set[str]], float] | None = None
_l1_metrics: tuple[list[dict], float] | None = None


def _l1_fresh(bucket: list) -> bool:
    """注意判的是 bucket[0] 不是 bucket：调用方传 [全局变量]，未命中时是
    [None]——早期版只判 bool(bucket)，[None] 也"非空"，None[1] 直接
    TypeError 把 schema_curator 节点炸掉（全量测试踩出）。"""
    return bool(bucket) and bucket[0] is not None and bucket[0][1] > time.monotonic()


def _l1_invalidate() -> None:
    """进程内缓存失效（invalidate_cache 时调用，本 worker 立即生效）。"""
    global _l1_allowed, _l1_metrics
    _l1_allowed = None
    _l1_metrics = None


async def load_allowed_tables(use_cache: bool = True) -> dict[str, set[str]]:
    """返回 {表名: {列名集合}}，如 {"users": {"id", "name", ...}, ...}。"""
    global _l1_allowed
    if use_cache and _l1_fresh([_l1_allowed]):
        return {t: set(cols) for t, cols in _l1_allowed[0].items()}
    if use_cache:
        cached = await cache_get(CACHE_KEY)
        if cached:
            out = {t: set(cols) for t, cols in cached.items()}
            _l1_allowed = (out, time.monotonic() + L1_TTL_S)
            return {t: set(cols) for t, cols in out.items()}

    async with admin_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'biz'
            ORDER BY table_name, ordinal_position
            """
        )
    tables: dict[str, set[str]] = {}
    for r in rows:
        tables.setdefault(r["table_name"], set()).add(r["column_name"])

    if use_cache and tables:
        await cache_set(CACHE_KEY, {t: sorted(cols) for t, cols in tables.items()}, CACHE_TTL_S)
        _l1_allowed = (tables, time.monotonic() + L1_TTL_S)
    log.info("schema.loaded", extra={"context": {"tables": sorted(tables)}})
    return tables


async def invalidate_cache() -> None:
    """DDL 变更后调用（灌数据脚本/迁移流程结尾）。本进程 L1 立即失效，
    Redis 两级 key 也删（其他 worker 的 L1 最多 30s 后自然过期）。"""
    _l1_invalidate()
    await get_redis().delete(CACHE_KEY, METRICS_CACHE_KEY)


METRICS_CACHE_KEY = "schema:metric_definitions"


async def load_metric_definitions(use_cache: bool = True) -> list[dict]:
    """指标口径注册表（Agent 的"业务知识"，SchemaCurator 的检索对象）。

    D2 会灌入 embedding 做向量检索；当前先返回全量（量小，<100 条）。
    带 Redis 缓存（多级缓存第二级）：口径表每条问数都会全量注入 prompt，
    但内容几乎不变——与 schema 注册表同理，缓存 miss 才查库。
    重灌 mock 数据/改口径后 invalidate_cache() 会连它一起清。
    """
    global _l1_metrics
    if use_cache and _l1_fresh([_l1_metrics]):
        return list(_l1_metrics[0])
    if use_cache:
        cached = await cache_get(METRICS_CACHE_KEY)
        if cached:
            _l1_metrics = (list(cached), time.monotonic() + L1_TTL_S)
            return cached
    async with admin_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT metric_name, definition, sql_hint
            FROM biz.metric_definitions
            ORDER BY metric_name
            """
        )
    metrics = [dict(r) for r in rows]
    if use_cache and metrics:
        await cache_set(METRICS_CACHE_KEY, metrics, CACHE_TTL_S)
        _l1_metrics = (metrics, time.monotonic() + L1_TTL_S)
    return metrics
