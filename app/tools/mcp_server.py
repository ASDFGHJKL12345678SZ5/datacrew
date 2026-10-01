"""MCP 工具服务：把 DataCrew 的工具能力以 MCP 协议标准暴露。

为什么用 MCP 而不是项目私有函数（面试可讲）：
    工具即标准协议。同一个 sql_execute，Claude Desktop / Cline / LangGraph agent
    都能即插即用，不需要为每个客户端写适配代码。工具从"项目私有资产"变成
    "可复用生态组件"——这就是 MCP 的核心价值。

传输方式：
    stdio —— 本地进程间通信（LangGraph agent 默认走这个）
    SSE   —— 远程 HTTP（D2 演示"工具服务独立部署"时启用）

启动：
    python -m app.tools.mcp_server            # stdio 模式
    python -m app.tools.mcp_server --sse      # SSE 模式（端口 8001）
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from app.core.logging import get_logger, setup_logging
from app.infra.db import close_pools, init_pools
from app.tools.chart_gen import generate_chart
from app.tools.python_sandbox import run_python
from app.tools.schema_search import search_schema
from app.tools.sql_execute import execute_sql

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_server: FastMCP):
    """服务启停时管理数据库连接池。"""
    setup_logging()
    await init_pools()
    log.info("mcp.server.started")
    try:
        yield
    finally:
        await close_pools()
        log.info("mcp.server.stopped")


mcp = FastMCP("datacrew-tools", lifespan=lifespan)


@mcp.tool()
async def sql_execute(sql: str, approved: bool = False) -> dict:
    """安全执行一条 SQL（只读）。

    经过七道安全闸：AST 解析、单语句、只读、表/列白名单、危险函数、
    嵌套深度、大表审批。自动包裹 LIMIT，只读事务 + 3s 超时。
    对大表（orders/order_items/traffic_logs）的无过滤条件查询需要人工审批
    （approved=True 表示已获批准）。

    Args:
        sql: 待执行的 SQL（必须是 SELECT）
        approved: 大表无过滤查询是否已获人工批准

    Returns:
        {"ok": bool, "columns": [...], "rows": [...], "error": str?, ...}
    """
    return await execute_sql(sql, approved=approved)


@mcp.tool()
async def schema_search(question: str, top_k: int = 5) -> dict:
    """检索与问题相关的数据表和指标口径定义。

    问数第一步：先找到该查哪些表、"销售额"这类指标的口径是什么，
    避免 LLM 猜测表名和指标含义。

    Args:
        question: 用户的自然语言问题
        top_k: 返回的相关表数量上限

    Returns:
        {"tables": [{"name", "columns", "score"}], "metrics": [{...}]}
    """
    return await search_schema(question, top_k)


@mcp.tool()
async def python_sandbox(code: str, timeout_s: int = 10) -> dict:
    """在隔离进程中执行 Python 数据分析代码。

    安全边界：隔离解释器（-I）+ socket 禁网守卫 + setrlimit 资源限额
    （Linux）+ 超时强杀；完整容器级禁网见部署文档（network:none）。
    代码应 print 输出结果；返回 {"ok", "stdout", "error_type"}。

    Args:
        code: 待执行的 Python 源码（print 输出分析结果）
        timeout_s: 超时秒数（1-30，默认 10，到点强杀进程）

    Returns:
        {"ok": bool, "stdout": str, "error_type": "timeout|blocked|error|ok", ...}
    """
    return await run_python(code, timeout_s=timeout_s)


@mcp.tool()
async def chart_gen(chart_type: str, data: list, title: str = "") -> dict:
    """把查询结果渲染成 SVG 图表（bar/line/pie），落对象存储返回 URL。

    纯 Python 渲染、零依赖、确定性输出；输入对齐 sql_execute 的结果结构。

    Args:
        chart_type: bar（柱状）| line（折线） | pie（饼图）
        data: [["标签", 数值], ...] 或 [{"label": ..., "value": ...}]，≤20 个点
        title: 图表标题

    Returns:
        {"ok": bool, "url": str, "key": str, "format": "svg", ...}
    """
    return await generate_chart(chart_type, data, title=title)


if __name__ == "__main__":
    if "--sse" in sys.argv:
        mcp.settings.port = 8001
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
