"""偏好记忆评测探针：度量 **preference_hit**（记忆是否真的生效）。

为什么单列一个探针而不是混进 120 条主评测集：
主评测集是**单轮**设计（每题一个独立 session，行为类题目的通过标准就是"触发澄清"）。
把多轮记忆塞进去会污染那 120 条的语义。这里用独立的双轮场景度量，互不干扰。

每个场景两轮 + 一个反向对照：
    第 1 轮：无记忆 -> 必须澄清 -> 用户回答 -> 必须出结果（同时沉淀记忆）
    第 2 轮：同 session 再问同一问题 -> **不应再追问**且口径沿用 -> 记 preference_hit
    反向对照：**全新 session** 问同一问题 -> 必须仍然澄清
             （这条是防"记忆污染新会话"的守卫：一旦失败，主评测集的行为类会静默全挂）

用法：
    python eval/preference_probe.py            # 全部场景
    python eval/preference_probe.py --limit 1  # 冒烟
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.application.ask_service import ask, resume  # noqa: E402
from app.core.winloop import ensure_selector_loop  # noqa: E402
from app.infra.db import close_pools, init_pools  # noqa: E402
from app.infra.memory import (  # noqa: E402
    ensure_memory_schema,
    forget_preferences,
    load_preferences,
    memory_stats,
)

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# 场景：问题（mock curator 对含"销售额"的问题必报歧义）+ 用户澄清时选的口径
SCENARIOS: list[dict[str, str]] = [
    {"question": "上个月各渠道的销售额是多少", "choice": "实付销售额", "term": "销售额"},
    {"question": "销售额最高的渠道是哪个", "choice": "GMV", "term": "销售额"},
    {"question": "最近 7 天的销售额趋势", "choice": "实付销售额", "term": "销售额"},
]


async def _collect(agen) -> list[dict]:
    return [ev async for ev in agen]


async def run_scenario(idx: int, sc: dict[str, str]) -> dict:
    sid = f"pref-probe-{idx}"
    fresh_sid = f"pref-probe-{idx}-fresh"
    await forget_preferences(sid)
    await forget_preferences(fresh_sid)

    # ---- 第 1 轮：澄清 + 回答 + 出结果 ----
    events = await _collect(ask(sc["question"], sid))
    clarification = next((e for e in events if e["event"] == "clarification"), None)
    if clarification is None:
        return {"id": idx, "question": sc["question"], "hit": False,
                "reason": "第 1 轮未触发澄清（场景本身失效）"}

    events2 = await _collect(resume(sid, sc["choice"]))
    if not any(e["event"] == "result" for e in events2):
        return {"id": idx, "question": sc["question"], "hit": False,
                "reason": "澄清后未产出结果"}

    remembered = await load_preferences(sid)
    if remembered.get(sc["term"]) != sc["choice"]:
        return {"id": idx, "question": sc["question"], "hit": False,
                "reason": f"记忆未沉淀（实际 {remembered}）"}

    # ---- 第 2 轮：同 session 再问 -> 不应再追问 ----
    events3 = await _collect(ask(sc["question"], sid))
    re_asked = any(e["event"] == "clarification" for e in events3)
    got_result = any(e["event"] == "result" for e in events3)
    applied = [
        e for e in events3
        if e.get("event") == "node_done" and (e.get("detail") or {}).get("preference_applied")
    ]

    # ---- 反向对照：全新 session 必须仍澄清 ----
    await forget_preferences(fresh_sid)
    events4 = await _collect(ask(sc["question"], fresh_sid))
    fresh_clarified = any(e["event"] == "clarification" for e in events4)

    hit = (not re_asked) and got_result and bool(applied)
    reason = "命中记忆：未重复追问且口径沿用" if hit else (
        "记忆命中但仍追问" if re_asked else "记忆命中但未出结果/无标记"
    )
    if not fresh_clarified:
        reason += "；⚠️ 新会话未被追问（记忆污染！）"

    stats = await memory_stats(sid)
    await forget_preferences(sid)
    await forget_preferences(fresh_sid)
    return {
        "id": idx, "question": sc["question"], "hit": hit, "reason": reason,
        "fresh_clarified": fresh_clarified, "hits": stats["hits"], "applied": bool(applied),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个场景（冒烟）")
    args = ap.parse_args()

    await init_pools()
    await ensure_memory_schema()
    records = []
    try:
        scenarios = SCENARIOS[: args.limit] if args.limit else SCENARIOS
        for i, sc in enumerate(scenarios, 1):
            rec = await run_scenario(i, sc)
            records.append(rec)
            mark = "✓" if rec["hit"] else "✗"
            print(f"{mark} [{i}] {rec['question']} — {rec['reason']}")
    finally:
        await close_pools()

    total = len(records)
    hits = sum(1 for r in records if r["hit"])
    fresh_ok = sum(1 for r in records if r.get("fresh_clarified"))
    print("")
    print(f"preference_hit          = {hits}/{total} ({hits / total:.0%})" if total else "无场景")
    print(f"fresh_session_guard     = {fresh_ok}/{total} (新会话仍澄清，记忆未污染)")


if __name__ == "__main__":
    ensure_selector_loop()
    asyncio.run(main())
