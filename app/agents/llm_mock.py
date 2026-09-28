"""Mock LLM：规则驱动的假模型，用于无 API Key 时测试状态机全链路。

为什么需要它（面试可讲）：
    核心编排逻辑（澄清/自愈/审批）不应该依赖外部 LLM 服务才能测试。
    Mock 让 CI 里每次提交都能跑完整状态机回归 —— 把"Agent 逻辑可测"
    从口号变成现实。真实 LLM 通过 LLM_MODE=real 切换，零代码改动。

Mock 的行为规则（确定性，可复现）：
    - SchemaCurator：问题含"销售额" -> 报告 GMV/实付 歧义（只看问题本体，
      不看 schema 上下文——上下文里永远有"销售额"，看它会导致任何问题都误报歧义）
    - SQLGenerator：按问题关键词选 SQL；首发生成用不存在的列 sale_amount（触发自愈），
      看到失败 SQL 里有 sale_amount 时返回修正版
    - InsightWriter：把结果格式化成结论文本
"""
from __future__ import annotations

import json
from typing import Any


def _extract_question(user: str) -> str:
    # user 消息格式："问题：{question}\n{schema上下文}"；修复模式则整段是错误回灌
    for line in user.split("\n"):
        if line.startswith("问题："):
            return line[len("问题："):].strip()
    return user


def mock_chat(messages: list[dict[str, str]], **_: Any) -> str:
    # 按消息内容路由到对应的规则处理器，返回 JSON 字符串（与真实 LLM 输出格式一致）
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")

    if "数据规划专家" in system:
        return _curator(_extract_question(user))
    if "PostgreSQL 专家" in system:
        return _sql_gen(user)
    if "数据分析师" in system:
        return _insight(user)
    return json.dumps({"content": "mock: 未识别的请求"}, ensure_ascii=False)


def _curator(question: str) -> str:
    # SchemaCurator：问题含"销售额" -> 歧义；否则直接给表
    if "销售额" in question:
        return json.dumps({
            "tables": ["orders"],
            "metric": None,
            "ambiguity": {
                "term": "销售额",
                "options": [
                    "GMV（成交总额，含取消/退款单）",
                    "实付销售额（支付成功订单的实付金额）",
                ],
                "question": "您说的销售额是指 GMV（含取消/退款）还是实付销售额？",
            },
        }, ensure_ascii=False)
    if "订单" in question:
        return json.dumps({"tables": ["orders"], "metric": "下单用户数", "ambiguity": None},
                          ensure_ascii=False)
    return json.dumps({"tables": ["orders"], "metric": None, "ambiguity": None}, ensure_ascii=False)


def _sql_gen(user: str) -> str:
    # SQLGenerator：自愈判断看失败 SQL 内容，生成判断看问题本体
    if "执行失败" in user:
        if "sale_amount" in user:
            # 自愈路径：列名不存在 -> 改用真实列 pay_amount
            sql = ("SELECT channel, SUM(pay_amount) AS pay_total FROM biz.orders "
                   "WHERE order_status IN ('paid','refunded') GROUP BY channel")
            return json.dumps({
                "reasoning": (
                    "上次列名 sale_amount 不存在，"
                    "改用 schema_context 中的真实列 pay_amount"
                ),
                "sql": sql,
            }, ensure_ascii=False)
        return json.dumps(
            {"reasoning": "重试查询", "sql": "SELECT COUNT(*) AS cnt FROM biz.orders"},
            ensure_ascii=False,
        )

    question = _extract_question(user)
    if "订单列表" in question or "所有订单" in question:
        # 大表无 WHERE 的非聚合查询 -> 触发人工审批流
        return json.dumps({
            "reasoning": "用户要订单列表，直接查 orders 表",
            "sql": "SELECT id, pay_amount, order_status FROM biz.orders",
        }, ensure_ascii=False)
    if "销售额" in question:
        # 故意用错列（不存在于 biz.orders）—— 触发 executor 报错 -> 自愈
        return json.dumps({
            "reasoning": "按渠道汇总销售额",
            "sql": ("SELECT channel, SUM(sale_amount) AS s FROM biz.orders "
                    "WHERE order_status IN ('paid','refunded') GROUP BY channel"),
        }, ensure_ascii=False)
    return json.dumps({
        "reasoning": "统计下单用户数",
        "sql": "SELECT COUNT(DISTINCT user_id) AS cnt FROM biz.orders",
    }, ensure_ascii=False)


def _insight(user: str) -> str:
    # InsightWriter：从结果上下文提取关键数字生成结论
    if "app" in user:
        return (
            "各渠道实付销售额：app 2.58 亿元（占 55%）、miniapp 1.64 亿元、"
            "h5 0.47 亿元，app 为绝对主力渠道。"
        )
    return "查询完成，结果见数据表。"
