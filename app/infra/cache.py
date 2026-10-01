"""Redis 缓存与限流：token bucket + 通用 KV。

为什么用 Redis 而不是进程内字典：
1. 限流必须跨进程一致（API 多 worker 部署时进程内限流形同虚设）
2. 缓存可设置 TTL，进程重启不丢热数据
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def cache_get(key: str) -> Any | None:
    """读缓存；Redis 故障降级为 None（缓存是优化，不是依赖——最终审查修复：
    早期版 ConnectionError 一路炸穿 load_allowed_tables -> execute_sql ->
    executor_node -> SSE error，Redis 一挂整条问数链路硬失败）。"""
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        log.warning("cache.get_degraded", extra={"context": {"key": key, "reason": str(e)[:80]}})
        return None


async def cache_set(key: str, value: Any, ttl_s: int = 600) -> None:
    """写缓存；Redis 故障静默跳过（同上：降级不阻塞主链路）。"""
    try:
        await get_redis().set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl_s)
    except Exception as e:
        log.warning("cache.set_degraded", extra={"context": {"key": key, "reason": str(e)[:80]}})


async def token_bucket(key: str, capacity: int, refill_per_min: int) -> bool:
    """令牌桶限流：返回 True 放行 / False 拒绝。

    实现：Redis Hash 存 {tokens, ts}，Lua 脚本保证原子性（多 worker 安全）。
    """
    lua = """
    local k = KEYS[1]
    local now = tonumber(ARGV[1])
    local cap = tonumber(ARGV[2])
    local rate = tonumber(ARGV[3]) / 60.0
    local d = redis.call('HMGET', k, 'tokens', 'ts')
    local tokens = tonumber(d[1]) or cap
    local ts = tonumber(d[2]) or now
    tokens = math.min(cap, tokens + (now - ts) * rate)
    if tokens < 1 then
        redis.call('HMSET', k, 'tokens', tokens, 'ts', now)
        redis.call('EXPIRE', k, 3600)
        return 0
    end
    tokens = tokens - 1
    redis.call('HMSET', k, 'tokens', tokens, 'ts', now)
    redis.call('EXPIRE', k, 3600)
    return 1
    """
    try:
        ok = await get_redis().eval(lua, 1, key, _now(), capacity, refill_per_min)
        return bool(ok)
    except Exception as e:
        # 限流器故障时 fail-open（放行）：Redis 挂掉时拒掉所有请求比不限流
        # 更糟；可见性由 /health 的 redis:false 和这里的告警保证。
        log.warning("ratelimit.degraded_open", extra={"context": {"reason": str(e)[:80]}})
        return True


def _now() -> float:
    import time
    return time.time()


async def healthcheck() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False
