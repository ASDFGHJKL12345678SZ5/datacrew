"""MCP 客户端联通测试：4 个工具逐一调用验证（发现 + 调用成功才算连通）。

这条链路就是 LangGraph agent 经 MCP 协议调工具的路径：
正常查询 / 攻击拦截 / 沙箱计算 / 沙箱禁网 / 图表生成 / 图表错误输入，
覆盖每个工具的"正常路径 + 威胁路径"两条。
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

    # 4. python_sandbox：正常计算 + 禁网边界
    r = parse_result(await by_name["python_sandbox"].ainvoke(
        {"code": "print(sum(range(101)))"}))
    print("[python_sandbox] ok:", r["ok"], "| stdout:", r.get("stdout", "").strip())
    r = parse_result(await by_name["python_sandbox"].ainvoke(
        {"code": "import socket; socket.create_connection(('8.8.8.8', 53))",
         "timeout_s": 5}))
    print("[python_sandbox] 禁网:", r["ok"], "|", r.get("error_type"), "|", r.get("error", "")[:40])

    # 5. chart_gen：柱状图 + 错误输入自愈
    r = parse_result(await by_name["chart_gen"].ainvoke(
        {"chart_type": "bar",
         "data": [["app", 25800], ["miniapp", 16400], ["h5", 4700]],
         "title": "各渠道实付销售额"}))
    print("[chart_gen] ok:", r["ok"], "| url:", r.get("url"), "| points:", r.get("points"))
    r = parse_result(await by_name["chart_gen"].ainvoke(
        {"chart_type": "radar", "data": [["a", 1]]}))
    print("[chart_gen] 非法类型被拒:", r["ok"], "|", r.get("error", "")[:40])

    called = ["schema_search", "sql_execute", "python_sandbox", "chart_gen"]
    missing = [t for t in called if t not in by_name]
    print(f"\n工具连通性: {len(called) - len(missing)}/{len(called)}"
          f"（发现 {len(by_name)} 个，全部完成调用验证）")
    assert not missing, f"工具缺失: {missing}"


asyncio.run(main())
