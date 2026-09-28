"""PostgreSQL 连接管理：双连接池（管理员 / 只读）。

安全设计（面试可讲）：
- pg_ro 连接池固定使用 datacrew_ro 角色（只读、限 schema、读不到 eval）
- Agent 执行 SQL 只能走 pg_ro；灌数据/建评测集走 pg_admin
- 连接即权限：把"Agent 不能干什么"下沉到数据库层，而不是只靠应用层校验
"""
from __future__ import annotations

import asyncpg
from asyncpg import Pool

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_admin_pool: Pool | None = None
_ro_pool: Pool | None = None


async def init_pools() -> None:
    """应用启动时创建双连接池（FastAPI lifespan 调用）。"""
    global _admin_pool, _ro_pool
    s = get_settings()
    if _admin_pool is None:
        _admin_pool = await asyncpg.create_pool(
            s.pg_admin_dsn, min_size=2, max_size=s.pg_pool_max, command_timeout=30
        )
        log.info("pg.admin_pool.ready")
    if _ro_pool is None:
        # 只读池：Agent 的 SQL 执行唯一通道
        _ro_pool = await asyncpg.create_pool(
            s.pg_ro_dsn, min_size=s.pg_pool_min, max_size=s.pg_pool_max, command_timeout=30
        )
        log.info("pg.ro_pool.ready")


async def close_pools() -> None:
    global _admin_pool, _ro_pool
    for pool in (_admin_pool, _ro_pool):
        if pool is not None:
            await pool.close()
    _admin_pool = _ro_pool = None


def admin_pool() -> Pool:
    """管理员连接池：仅用于数据初始化/评测集管理，禁止 Agent 使用。"""
    if _admin_pool is None:
        raise RuntimeError("admin_pool 未初始化，请先调用 init_pools()")
    return _admin_pool


def ro_pool() -> Pool:
    """只读连接池：Agent 执行 SQL 的唯一通道（datacrew_ro 角色）。"""
    if _ro_pool is None:
        raise RuntimeError("ro_pool 未初始化，请先调用 init_pools()")
    return _ro_pool


async def healthcheck() -> dict[str, bool]:
    """探活：供 /health 端点与部署健康检查使用。"""
    result = {}
    for name, pool in (("admin", _admin_pool), ("ro", _ro_pool)):
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            result[name] = True
        except Exception:
            result[name] = False
    return result
