"""外部 agent demo：一个完全独立的进程，经 SSE 调用 datacrew 的 MCP 工具。

这个脚本扮演的就是"别人的 agent"：它不知道 datacrew 的任何内部实现，
只通过 MCP 协议拿到工具列表（= docstring）+ 调用。用法：

    python scripts/sse_agent_demo.py ["你的问题"]

输出是人类可读的决策流水（不是 LangChain 原始 chunk）：
    [决策] LLM 决定调用哪个工具、传什么参数   ← args 在这
    [返回] 工具经 MCP 送回的结果摘要
    [最终答案] LLM 组织好的回答                 ← 答案在这
"""
import asyncio
import json
import os
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

QUESTION = sys.argv[1] if len(sys.argv) > 1 else "上个月各渠道实付销售额是多少"

async def main():
    # 1. 连接：SSE 指向正在跑的 MCP server（不拉新进程，这就是"独立部署"的形态）
    client = MultiServerMCPClient({
        "datacrew": {"transport": "sse", "url": "http://127.0.0.1:8001/sse"}
    })
    # 2. 发现：拿到的每个工具都带着 @mcp.tool() 的 docstring 当"说明书"
    tools = await client.get_tools()
    print("agent 拿到的工具:", [t.name for t in tools])

    # 3. 装备：DeepSeek + LangChain ReAct agent（工具的"使用人"）
    llm = ChatOpenAI(
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key=os.environ["LLM_API_KEY"],
    )
    agent = create_react_agent(llm, tools)
    print(f"\n问题: {QUESTION}\n" + "-" * 60)

    # 4. 提问→决策→调用→答案：只打三帧，每帧都看得懂
    final_answer = None
    async for chunk in agent.astream({"messages": [{"role": "user", "content": QUESTION}]}):
        for _node, payload in chunk.items():
            for msg in payload.get("messages", []):
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        args = json.dumps(tc["args"], ensure_ascii=False)
                        print(f"[决策] 调用 {tc['name']}  args={args}")
                        if "approved" in tc["args"]:
                            print(f"       *** approved 参数出现了：{tc['args']['approved']} ***")
                elif msg.__class__.__name__ == "ToolMessage":
                    body = msg.content
                    if isinstance(body, list):
                        body = " ".join(str(b.get("text", b)) if isinstance(b, dict) else str(b) for b in body)
                    print(f"[返回] {msg.name} => {str(body)[:260]}")
                elif getattr(msg, "content", None):
                    final_answer = msg.content   # 最后一条 AI 发言才是终答

    print("-" * 60 + "\n[最终答案]")
    print(final_answer or "（agent 没有给出文字回答）")

asyncio.run(main())
