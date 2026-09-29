"""PostgreSQL checkpointer 的性能修正子类。

背景（D3 压测实锤，全部本机可复现，数据见 README 第 11 节）：
    langgraph 的 AsyncPostgresSaver 在写 checkpoint 时用 cur.executemany：
        - aput_writes：写 checkpoint writes 表
        - aput：状态里的非原始值（dict/list，如 schema_context、trace）进 blobs 表
    而 psycopg 的异步 executemany 内部走 pipeline 协议，本机（Windows +
    Docker Desktop PG）实测 pipeline 每次同步有 ~22ms 固定成本
    （空 pipeline 进出 22ms，pipeline+1 查询 44ms，同连接裸 execute 0.53ms）。
    一次图 invoke 触发 6 次 aput + 5 次 aput_writes，全部砸在 executemany 上，
    端到端 472ms、QPS 锁死 1.9、延迟随并发线性劣化。

修正：代理 cursor，把 executemany 换成逐行 execute。writes/blobs 表每行本是
独立 INSERT，逐行执行语义完全一致（autocommit 下每行独立提交），只是把
pipeline 的固定税换成 N 次廉价往返。实测端到端 472ms -> 260ms，
QPS 1.9 -> 3.3，P95（并发 20）10.2s -> 5.6s。

维护约定：只依赖父类 _cursor 的存在与 executemany 的调用形态，不复制上游
SQL 逻辑；langgraph 升级后若 executemany 从这两处消失，本类自动退化为
无操作（代理仍转发所有其他方法）。行为正确性由
tests/test_state_machine.py 的 interrupt/resume 用例守住——checkpoint
持久化坏了，澄清恢复会立刻挂。
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


class _NullLock:
    """可复用的空异步锁（池模式下替换父类的全局锁，见 FastAsyncPostgresSaver）。"""

    async def __aenter__(self) -> _NullLock:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FastCursor:
    """AsyncCursor 代理：executemany 逐行执行，其余方法原样转发。"""

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    async def executemany(self, query: str, params: Sequence[Sequence[Any]]) -> None:
        # psycopg 异步 executemany 内部走 pipeline 协议（每次同步 ~22ms 固定成本），
        # 逐行 execute 每次 ~1.3ms——行数本就少（一次 invoke 个位数），逐行更快。
        for row in params:
            await self._cur.execute(query, row)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)

    async def __aenter__(self) -> _FastCursor:
        if hasattr(self._cur, "__aenter__"):
            await self._cur.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        if hasattr(self._cur, "__aexit__"):
            return await self._cur.__aexit__(*exc)
        return None


class FastAsyncPostgresSaver(AsyncPostgresSaver):
    """两处性能修正（均有实测数据，见模块 docstring 与 README 第 11 节）：
    1. _cursor 产出的 cursor 包一层，executemany 逐行执行（去 pipeline 22ms 税）
    2. 池模式下把父类的全局 self.lock 换成空锁——池每次 _cursor 取独立连接，
       全局锁是多余的串行点（实测去锁后 QPS 随并发从 4 扩展到 55）
    """

    def __init__(self, conn: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(conn, *args, **kwargs)
        if isinstance(conn, AsyncConnectionPool):
            # 单连接模式必须保留真锁（psycopg 连接不能被并发使用）；
            # 池模式每次 _cursor 从池里取独立连接，锁只剩串行化副作用。
            self.lock = _NullLock()

    @asynccontextmanager
    async def _cursor(self, *, pipeline: bool = False) -> AsyncIterator[_FastCursor]:
        async with super()._cursor(pipeline=pipeline) as cur:
            yield _FastCursor(cur)
