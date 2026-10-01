"""压测：并发打 /ask（SSE 流式），产出 P50/P95/P99/QPS/错误率。

设计要点：
    1. SSE 响应必须读完整个流才算一次请求完成——用 aiter_lines 逐行消费，
       提前断开会让服务端的生成器半途而废，延迟数字失真。
    2. 并发上限 = 服务端 worker 的有效并发能力；本机单 worker 模式下，
       并发打太高压测的是排队而不是服务能力，所以默认 20 并发、100 请求。
    3. 失败分两类：传输层（连接拒绝/超时/非 200）与业务层（流里出现 error 事件），
       分开计数——前者是容量问题，后者是逻辑问题。
    4. mock LLM 下压的是"状态机 + DB + 限流 + SSE"这条链路的吞吐，
       真实模型的延迟主要花在 LLM 调用，数字要重新测。

用法：python scripts/load_test.py [--concurrency 20] [--total 100]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.core.winloop import ensure_selector_loop  # noqa: E402

ensure_selector_loop()

# 限流是产品特性（每 key 20 次/分钟），压测容量时用多 key 轮询把桶的容量放大，
# 否则测到的是限流器不是流水线。key 白名单见 .env 的 API_KEYS。
KEYS = [f"dev-key-{i:03d}" for i in range(1, 21)]
QUESTIONS = [
    "上个月有多少下单用户",
    "各渠道的订单量分别是多少",
    "平均订单金额是多少",
    "各城市的订单量排名",
    "各订单状态的订单数",
]


async def _one(client: httpx.AsyncClient, q: str, key: str, results: list) -> None:
    t0 = time.perf_counter()
    rec = {"latency_ms": 0.0, "ok": False, "kind": ""}
    # session_id 每请求唯一：复用同一 session 会让 checkpointer 命中"已澄清/
    # 已失败"的旧状态，测出来的是状态机记忆而不是流水线吞吐（D7 修复）。
    # 多 key 轮询同样原因：每 key 限流 20 次/分钟，固定 key 测的是限流器。
    session_id = f"load-{uuid.uuid4().hex[:12]}"
    try:
        async with client.stream(
            "POST", "/ask", json={"question": q, "session_id": session_id},
            headers={"X-API-Key": key}, timeout=30.0,
        ) as resp:
            if resp.status_code != 200:
                rec["kind"] = f"http_{resp.status_code}"
                return
            business_error = False
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    payload = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if payload.get("event") == "error":
                    business_error = True
            rec["ok"] = not business_error
            rec["kind"] = "business_error" if business_error else "ok"
    except httpx.HTTPError as e:
        rec["kind"] = f"transport:{type(e).__name__}"
    finally:
        rec["latency_ms"] = (time.perf_counter() - t0) * 1000
        results.append(rec)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--total", type=int, default=100)
    args = parser.parse_args()

    results: list[dict] = []
    sem = asyncio.Semaphore(args.concurrency)
    t_start = time.perf_counter()

    async def guarded(i: int) -> None:
        async with sem:
            await _one(client, QUESTIONS[i % len(QUESTIONS)], KEYS[i % len(KEYS)], results)

    async with httpx.AsyncClient(base_url=args.url) as client:
        await asyncio.gather(*(guarded(i) for i in range(args.total)))
    wall_s = time.perf_counter() - t_start

    lat = sorted(r["latency_ms"] for r in results)
    ok = [r for r in results if r["ok"]]
    transport_fail = [r for r in results if r["kind"].startswith("transport")]
    business_fail = [r for r in results if r["kind"] == "business_error"]
    qps = len(results) / wall_s if wall_s else 0.0

    def pct(p: float) -> float:
        return lat[min(int(len(lat) * p), len(lat) - 1)]

    print(f"总请求: {len(results)}  墙钟: {wall_s:.1f}s  QPS: {qps:.1f}")
    print(f"成功: {len(ok)} ({len(ok) / len(results):.1%})  "
          f"传输层失败: {len(transport_fail)}  业务层失败: {len(business_fail)}")
    print(f"延迟 P50: {pct(0.5):.0f}ms  P95: {pct(0.95):.0f}ms  "
          f"P99: {pct(0.99):.0f}ms  max: {lat[-1]:.0f}ms")
    if transport_fail:
        kinds = {r["kind"] for r in transport_fail}
        print(f"传输层失败类型: {kinds}")
    if business_fail:
        print(f"业务层失败样例: {business_fail[0]['kind']}")


if __name__ == "__main__":
    asyncio.run(main())
