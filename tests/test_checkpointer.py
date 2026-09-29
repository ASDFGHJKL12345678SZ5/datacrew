"""FastAsyncPostgresSaver 的两处性能修正的行为测试。

背景：psycopg 异步 executemany 走 pipeline 协议（本机每次同步约 22ms 固定
成本），checkpoint 写入被它拖慢 4 倍以上；单连接 + 全局锁让 QPS 锁死。
这两个测试守住修正不被回归——改了这里，压测数字会悄悄退化。
"""
import asyncio

from app.infra.checkpointer import FastAsyncPostgresSaver, _FastCursor


class _FakeInner:
    """记录调用的假 AsyncCursor。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, query, params=None):
        self.calls.append(("execute", (query, params)))

    async def fetchone(self):
        return {"ok": True}


class TestFastCursor:
    def test_executemany_逐行执行且不碰_pipeline(self):
        """executemany 必须逐行 execute——psycopg 的 executemany 内部走 pipeline。"""
        inner = _FakeInner()
        cur = _FastCursor(inner)
        asyncio.run(cur.executemany("INSERT INTO t VALUES (%s)", [(1,), (2,), (3,)]))
        assert len(inner.calls) == 3
        assert all(c[0] == "execute" for c in inner.calls)
        assert [c[1][1] for c in inner.calls] == [(1,), (2,), (3,)]

    def test_其他方法原样转发(self):
        inner = _FakeInner()
        cur = _FastCursor(inner)
        assert asyncio.run(cur.fetchone()) == {"ok": True}
        asyncio.run(cur.execute("SELECT 1"))
        assert inner.calls[-1] == ("execute", ("SELECT 1", None))


class TestPoolModeLock:
    def test_池模式去掉全局锁(self):
        """池模式下每次 _cursor 取独立连接，父类全局锁只剩串行化副作用。"""
        from psycopg_pool import AsyncConnectionPool

        async def scenario():
            pool = AsyncConnectionPool(conninfo="postgres://x/y", open=False)
            saver = FastAsyncPostgresSaver(pool)
            # 空锁可重入（父类的 asyncio.Lock 在这里会死锁）
            async with saver.lock:
                async with saver.lock:
                    return type(saver.lock).__name__

        name = asyncio.run(scenario())
        assert name == "_NullLock"

    def test_单连接模式保留真锁(self):
        """单连接下 psycopg 连接不能被并发使用，锁必须保留。"""
        import asyncio as aio

        class _FakeConn:
            pass

        async def scenario():
            return type(FastAsyncPostgresSaver(_FakeConn()).lock).__name__

        assert asyncio.run(scenario()) == "Lock"
        assert isinstance(aio.Lock(), aio.Lock)
