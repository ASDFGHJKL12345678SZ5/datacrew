"""把口径表的唯一事实源（scripts/generate_mock_data.py 的 METRIC_DEFINITIONS）
同步到运行库 biz.metric_definitions。

为什么需要它：口径表有两条落地路径——
  1. **全新环境**：generate_mock_data.py 建表 + 灌数据时一并写入；
  2. **已有环境**：改口径时不能重跑 seeder（它会 TRUNCATE 业务数据、把评测集哈希全部作废），
     必须有一条**只动口径、不碰业务数据**的通道——就是这个脚本。

幂等：按 metric_name upsert，可反复执行。

用法：
    python scripts/sync_metric_definitions.py --dry-run   # 只看差异，不写库
    python scripts/sync_metric_definitions.py             # 执行同步（含 Redis 缓存失效）
    python scripts/sync_metric_definitions.py --prune     # 额外删除库里多出的口径
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_mock_data import METRIC_DEFINITIONS  # noqa: E402

from app.infra.cache import close_redis, get_redis  # noqa: E402
from app.infra.db import admin_pool, close_pools, init_pools  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

UPSERT = """
INSERT INTO biz.metric_definitions (metric_name, definition, sql_hint, usage_hint)
VALUES ($1, $2, $3, $4)
ON CONFLICT (metric_name) DO UPDATE
   SET definition = EXCLUDED.definition,
       sql_hint   = EXCLUDED.sql_hint,
       usage_hint = EXCLUDED.usage_hint
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印差异")
    ap.add_argument("--prune", action="store_true", help="删除库里多出的口径")
    args = ap.parse_args()

    source = {name: (definition, hint, usage)
              for name, definition, hint, usage in METRIC_DEFINITIONS}

    await init_pools()
    try:
        async with admin_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT metric_name, definition, sql_hint, usage_hint FROM biz.metric_definitions"
            )
            current = {r["metric_name"]: (r["definition"], r["sql_hint"], r["usage_hint"])
                       for r in rows}

            added = [n for n in source if n not in current]
            updated = [n for n in source if n in current and current[n] != source[n]]
            unchanged = [n for n in source if n in current and current[n] == source[n]]
            extra = [n for n in current if n not in source]

            print(f"源口径 {len(source)} 条 | 新增 {len(added)} | 更新 {len(updated)} "
                  f"| 未变 {len(unchanged)} | 库里多出 {len(extra)}")
            for label, names in (("新增", added), ("更新", updated), ("多出", extra)):
                if names:
                    print(f"  {label}: " + " / ".join(names))

            if args.dry_run:
                print("（dry-run，未写库）")
                return

            for name in added + updated:
                await conn.execute(UPSERT, name, *source[name])
            if args.prune and extra:
                await conn.execute(
                    "DELETE FROM biz.metric_definitions WHERE metric_name = ANY($1::text[])", extra
                )
                print(f"已删除多出口径 {len(extra)} 条")

            final = await conn.fetchval("SELECT count(*) FROM biz.metric_definitions")
            print(f"落库完成，库内共 {final} 条")

        # 口径表被 Redis 缓存（L2）与进程内 L1 缓存——必须失效，否则改了看不到
        await get_redis().delete("schema:metric_definitions", "schema:allowed_tables")
        print("Redis 缓存已失效（schema:metric_definitions / schema:allowed_tables）")
    finally:
        await close_pools()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
