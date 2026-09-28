"""事件循环工厂：Windows 下把 uvicorn 的循环从 Proactor 换成 Selector。

背景（README ADR 有完整记录）：
    uvicorn 在 Windows 上默认强制 ProactorEventLoop（为了子进程 worker 支持），
    而 psycopg 的异步模式在 Proactor 下直接报
    "Psycopg cannot use the 'ProactorEventLoop'"。
    本服务没有子进程 worker 需求，用 Selector 循环换取 psycopg 异步兼容。

注入方式：uvicorn.run(loop="app.loops:selector_loop_factory")。
注意契约：自定义 loop 工厂会被 asyncio.Runner 无参调用，
必须返回事件循环【实例】（不是类）。
"""
import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()
