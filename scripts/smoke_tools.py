"""D1 工具层冒烟测试：连真库，走全链路。"""
import asyncio

from app.core.logging import setup_logging
from app.infra.db import close_pools, healthcheck, init_pools
from app.tools.schema_search import search_schema
from app.tools.sql_execute import execute_sql


async def main() -> None:
    setup_logging("WARNING")
    await init_pools()
    try:
        print("== 0. 连接池健康 ==", await healthcheck())

        print("\n== 1. schema_search：上个月各渠道实付销售额 ==")
        s = await search_schema("各渠道实付销售额 orders pay_amount")
        print("相关表:", [t["name"] for t in s["tables"]])
        print("命中口径:", [m["metric_name"] for m in s["metrics"]])

        print("\n== 2. 正常业务查询（聚合，应放行）==")
        r = await execute_sql(
            "SELECT channel, SUM(pay_amount) AS pay_total, COUNT(*) AS cnt "
            "FROM biz.orders WHERE order_status IN ('paid','refunded') "
            "AND pay_time >= '2026-08-01' AND pay_time < '2026-09-01' GROUP BY channel"
        )
        print("ok:", r["ok"], "| 行数:", r.get("row_count"), "| 延迟:", r.get("latency_ms"), "ms")
        for row in r.get("rows", [])[:5]:
            print("   ", row)

        print("\n== 3. 攻击1：多语句注入（应拒绝）==")
        r = await execute_sql("SELECT id FROM biz.users; DROP TABLE biz.users;")
        print("ok:", r["ok"], "| error_type:", r.get("error_type"), "|", r.get("error", "")[:60])

        print("\n== 4. 攻击2：pg_sleep 拖库（应拒绝）==")
        r = await execute_sql("SELECT id FROM biz.users WHERE pg_sleep(5) IS NULL")
        print("ok:", r["ok"], "| error_type:", r.get("error_type"), "|", r.get("error", "")[:60])

        print("\n== 5. 攻击3：未授权表（应拒绝）==")
        r = await execute_sql("SELECT gold_sql FROM eval.queries")
        print("ok:", r["ok"], "| error_type:", r.get("error_type"), "|", r.get("error", "")[:60])

        print("\n== 6. 大表无 WHERE（应转人工审批）==")
        r = await execute_sql("SELECT id, pay_amount FROM biz.orders")
        print("ok:", r["ok"], "| error_type:", r.get("error_type"), "|", r.get("error", "")[:60])

        print("\n== 7. 审批后执行（应放行并自动 LIMIT）==")
        r = await execute_sql("SELECT id, pay_amount FROM biz.orders", approved=True)
        print("ok:", r["ok"], "| 行数:", r.get("row_count"), "| truncated:", r.get("truncated"))

        print("\n== 8. 报错自愈素材：错误 SQL 返回结构化错误 ==")
        r = await execute_sql("SELECT not_exist_col FROM biz.users")
        print("ok:", r["ok"], "| error_type:", r.get("error_type"), "|", r.get("error", "")[:80])
    finally:
        await close_pools()


asyncio.run(main())
