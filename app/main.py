"""DataCrew 服务入口。

为什么需要这个文件（而不是 python -m uvicorn）：
    uvicorn 在 Windows 上默认强制 ProactorEventLoop，而 psycopg 异步模式
    （langgraph checkpointer 依赖它）在 Proactor 下无法工作。本入口通过
    loop="app.loops:selector_loop_factory" 把循环换成 Selector。
    Linux/macOS 默认就是 SelectorEventLoop，这个入口在那边同样正常工作。

用法：python -m app.main
"""
import asyncio
import sys

if sys.platform == "win32":
    # 双保险：策略层也切 Selector（覆盖 asyncio.run 等其它入口）
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=8000,
        loop="app.loops:selector_loop_factory",
    )
