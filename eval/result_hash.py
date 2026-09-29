"""结果集归一化与哈希：评测比对的唯一事实源。

build_eval_set（算金标哈希）和 runner（算 Agent 结果哈希）必须用同一套逻辑，
否则"对不上"可能是归一化差异而不是答案错误——这是评测系统最常见的事故。
规则（对应三类假差异）：
    1. 数值统一 2 位小数字符串   -> 消除 1.5 vs 1.50
    2. 行内值排序 + 全部行排序   -> 消除列顺序 / ORDER BY 不同造成的假失败
       （text-to-SQL 评测通行做法是结果集集合比对，见 Spider 等基准）
    3. SHA256

两侧输入形态不同是正常的：金标侧是 asyncpg Record（dict-like），
Agent 侧经过 SSE/JSON 序列化是 list —— 本模块统一兼容。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

NL = chr(10)


def normalize_rows(rows: list) -> list[str]:
    out: list[str] = []
    for r in rows:
        vals = list(r.values()) if isinstance(r, dict) else list(r)
        norm = []
        for v in vals:
            if isinstance(v, (int, float, Decimal)):
                v = f"{round(float(v), 2):.2f}"
            elif v is None:
                v = "NULL"
            norm.append(str(v))
        out.append("|".join(sorted(norm)))
    return sorted(out)


def hash_rows(rows: list) -> str:
    payload = NL.join(normalize_rows(rows))
    return hashlib.sha256(payload.encode()).hexdigest()
