"""API 端到端冒烟：鉴权 / SSE 问数 / 澄清恢复 / 审批恢复。

前置：服务已启动（python -m app.main），Docker 里 PG/Redis 健康。
用法：python scripts/smoke_api.py
"""
import asyncio
import json

import httpx

BASE = "http://127.0.0.1:8000"
KEY = {"X-API-Key": "dev-key-001"}


async def read_sse(resp: httpx.Response) -> list[dict]:
    events = []
    event_type = None
    async for line in resp.aiter_lines():
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: ") and event_type:
            events.append({"event": event_type, "data": json.loads(line[6:])})
            event_type = None
    return events


def _show(events: list[dict]) -> dict | None:
    for e in events:
        d = e["data"]
        if e["event"] == "node_done":
            print(f"   [node] {d['node']} ({d.get('latency_ms')}ms)")
        elif e["event"] == "result":
            print(f"   [结果] {d['summary'][:40]}... | 行数={d['row_count']}")
        else:
            print(f"   [{e['event']}] {str(d)[:70]}")
    return next((e["data"] for e in events if e["event"] == "result"), None)


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        # 1. 鉴权：无 key / 错 key 都必须是 401
        r = await c.post("/ask", json={"question": "test"})
        assert r.status_code == 401, f"无 key 应 401，实际 {r.status_code}"
        r = await c.post("/ask", json={"question": "test"}, headers={"X-API-Key": "wrong"})
        assert r.status_code == 401, f"错 key 应 401，实际 {r.status_code}"
        print("1. 鉴权 401 x2 通过")

        # 2. 正常问数（SSE 四节点流式）
        async with c.stream(
            "POST", "/ask",
            json={"question": "上个月有多少下单用户", "session_id": "smoke-s1"},
            headers=KEY,
        ) as resp:
            events = await read_sse(resp)
        result = _show(events)
        assert result and result["row_count"] >= 1, "问数应有结果"
        nodes = [e["data"]["node"] for e in events if e["event"] == "node_done"]
        assert nodes == ["schema_curator", "sql_generator", "executor", "insight_writer"]
        print("2. 问数四节点流式通过")

        # 3. 澄清流：歧义 -> 中断 -> 恢复 -> 自愈 -> 结果
        async with c.stream(
            "POST", "/ask",
            json={"question": "上个月各渠道的销售额是多少", "session_id": "smoke-s2"},
            headers=KEY,
        ) as resp:
            events = await read_sse(resp)
        clarify = next((e for e in events if e["event"] == "clarification"), None)
        assert clarify, "歧义问题应触发澄清中断"
        print(f"3. 澄清中断: {clarify['data']['question'][:30]}...")
        async with c.stream(
            "POST", "/ask/resume",
            json={"session_id": "smoke-s2", "value": "实付销售额"},
            headers=KEY,
        ) as resp:
            events2 = await read_sse(resp)
        result = _show(events2)
        assert result, "恢复后应出结果"
        nodes = [e["data"]["node"] for e in events2 if e["event"] == "node_done"]
        assert nodes.count("sql_generator") == 2, "自愈合重试应发生"
        print("3. 澄清恢复+自愈通过")

        # 4. 审批流：大表无过滤 -> 中断 -> 批准 -> LIMIT 兜底
        async with c.stream(
            "POST", "/ask",
            json={"question": "给我所有订单列表", "session_id": "smoke-s3"},
            headers=KEY,
        ) as resp:
            events = await read_sse(resp)
        approval = next((e for e in events if e["event"] == "approval"), None)
        assert approval, "大表无过滤查询应触发审批中断"
        print(f"4. 审批中断: {approval['data']['reason'][:30]}...")
        async with c.stream(
            "POST", "/ask/resume",
            json={"session_id": "smoke-s3", "value": {"approved": True}},
            headers=KEY,
        ) as resp:
            events2 = await read_sse(resp)
        result = _show(events2)
        assert result and 0 < result["row_count"] <= 1000, "批准后应带 LIMIT 执行"
        print("4. 审批恢复+LIMIT 兜底通过")

    print("\nAPI 冒烟全部通过")


asyncio.run(main())
