"""Schema 注册表：Agent 可见的表/列白名单（安全闸闸4/闸5 的数据源）。

加载链：PostgreSQL information_schema 内省 → Redis 缓存（TTL 1h）→ 调用方
为什么缓存：schema 不变就不重复内省；缓存 miss 才查库（设计文档的"三级缓存"第一级）。
为什么从 DB 内省而不是手写配置：手写配置会随 DDL 漂移失效，内省永远与真实 schema 一致。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.infra.cache import cache_get, cache_set, get_redis
from app.infra.db import admin_pool

log = get_logger(__name__)

CACHE_KEY = "schema:allowed_tables"
CACHE_TTL_S = 3600


async def load_allowed_tables(use_cache: bool = True) -> dict[str, set[str]]:
    """返回 {表名: {列名集合}}，如 {"users": {"id", "name", ...}, ...}。"""
    if use_cache:
        cached = await cache_get(CACHE_KEY)
        if cached:
            return {t: set(cols) for t, cols in cached.items()}

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
    log.info("schema.loaded", extra={"context": {"tables": sorted(tables)}})
    return tables


async def invalidate_cache() -> None:
    """DDL 变更后调用（灌数据脚本/迁移流程结尾）。"""
    await get_redis().delete(CACHE_KEY)


async def load_metric_definitions() -> list[dict]:
    """指标口径注册表（Agent 的"业务知识"，SchemaCurator 的检索对象）。

    D2 会灌入 embedding 做向量检索；当前先返回全量（量小，<100 条）。
    """
    async with admin_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT metric_name, definition, sql_hint
            FROM biz.metric_definitions
            ORDER BY metric_name
            """
        )
    return [dict(r) for r in rows]
