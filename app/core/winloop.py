"""Windows 事件循环兼容：psycopg 异步模式（langgraph checkpointer）只能用 Selector 循环。

两类入口两种注法：
    - uvicorn 服务：app/main.py 通过 loop="app.loops:selector_loop_factory" 注入
    - 普通 asyncio.run 脚本（eval/runner.py 等）：入口处调用 ensure_selector_loop()
Linux/macOS 默认就是 SelectorEventLoop，此调用是 no-op。
"""
import asyncio
import sys


def ensure_selector_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
