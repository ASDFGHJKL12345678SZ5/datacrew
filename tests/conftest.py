"""共享测试夹具。

**为什么要在这里清记忆**：`mem.preferences` 是**跨轮持久**的长期记忆——上一轮澄清
会把"销售额=实付"沉淀下来，同一个 session 下一轮就不再追问。测试若不清，
"应触发澄清"的断言会在**第二次运行**时假失败（第一次写记忆、第二次命中记忆）。
测试必须自己隔离持久状态，而不是假设"库是干净的"。
"""
from __future__ import annotations

import pytest

from app.infra.cache import close_redis
from app.infra.db import close_pools, init_pools
from app.infra.memory import ensure_memory_schema, forget_preferences

# 集成测试使用的会话 ID（test_state_machine / test_memory 共用同一批约定）
TEST_SESSIONS = (
    "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "mem-loop", "mem-hit", "mem-fresh", "mem-explicit",
)


@pytest.fixture()
async def db_pools():
    """每个测试独立的 PG 池 + Redis 连接（跨事件循环复用会报 Event loop is closed），
    并清空测试会话的记忆（跨轮持久状态必须隔离）。"""
    await init_pools()
    await ensure_memory_schema()
    for sid in TEST_SESSIONS:
        await forget_preferences(sid)
    yield
    await close_pools()
    await close_redis()
