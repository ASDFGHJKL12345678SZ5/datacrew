"""MCP 客户端联通测试：通过 langchain-mcp-adapters 调用 MCP 工具服务。

这条链路就是 D2 LangGraph agent 调工具的路径 —— 提前验证，D2 不踩坑。
"""
import asyncio
import json
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient


def parse_result(r: object) -> dict:
    """MCP 工具返回的可能是 dict / JSON 字符串 / content block 列表，统一解析成 dict。"""
    if isinstance(r, dict):
        return r
    if isinstance(r, str):
        return json.loads(r)
    if isinstance(r, list) and r and isinstance(r[0], dict) and "text" in r[0]:
        return json.loads(r[0]["text"])
    return {"raw": r}


async def main() -> None:
    client = MultiServerMCPClient(
        {
            "datacrew": {
                "command": sys.executable,
                "args": ["-m", "app.tools.mcp_server"],
                "transport": "stdio",
            }
        }
    )
    tools = await client.get_tools()
    by_name = {t.name: t for t in tools}
    print("MCP 服务暴露的工具:", sorted(by_name))

    # 1. schema_search
    r = parse_result(await by_name["schema_search"].ainvoke(
        {"question": "上个月各渠道实付销售额", "top_k": 3}))
    print("\n[schema_search] 相关表:", [t["name"] for t in r["tables"]])

    # 2. sql_execute 正常查询
    r = parse_result(await by_name["sql_execute"].ainvoke(
        {"sql": "SELECT channel, SUM(pay_amount) AS s FROM biz.orders GROUP BY channel"}))
    print("[sql_execute] ok:", r["ok"], "| 行数:", r["row_count"], "| 数据:", r["rows"])

    # 3. sql_execute 攻击拦截
    r = parse_result(await by_name["sql_execute"].ainvoke(
        {"sql": "SELECT gold_sql FROM eval.queries"}))
    print("[sql_execute] 攻击拦截:", r["ok"], "|", r["error"][:50])


asyncio.run(main())
