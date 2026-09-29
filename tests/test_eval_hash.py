"""评测比对逻辑的不变量测试：哈希是执行类评测的信任根，必须有回归保障。

不变量：
    1. dict 形态（asyncpg Record，金标侧）与 list 形态（JSON 序列化，Agent 侧）同哈希
    2. 行序打乱不影响哈希（吸收 ORDER BY 差异）
    3. 列序打乱不影响哈希（吸收 SELECT 列顺序差异）
    4. 数值格式归一（1.5 与 1.50、233851 与 233851.0 同哈希）
外加端到端防漂移：库内金标哈希 == 当前代码重算哈希。
"""
from __future__ import annotations

import random

import pytest

from app.infra.db import admin_pool, close_pools, init_pools
from eval.result_hash import hash_rows

pytestmark = pytest.mark.usefixtures("db_pools")

ROWS = [
    {"channel": "app", "cnt": 233851},
    {"channel": "h5", "cnt": 69056},
    {"channel": "miniapp", "cnt": 197093},
]


@pytest.fixture()
async def db_pools():
    await init_pools()
    yield
    await close_pools()


def test_dict_and_list_forms_hash_identically() -> None:
    # 金标侧是 asyncpg Record（dict-like），Agent 侧经 SSE/JSON 是 list
    as_dicts = [dict(r) for r in ROWS]
    as_lists = [list(r.values()) for r in ROWS]
    assert hash_rows(as_dicts) == hash_rows(as_lists)


def test_row_order_does_not_matter() -> None:
    shuffled = ROWS[:]
    random.Random(42).shuffle(shuffled)
    assert hash_rows([list(r.values()) for r in shuffled]) == hash_rows(
        [list(r.values()) for r in ROWS]
    )


def test_column_order_does_not_matter() -> None:
    # 同一行内两列互换位置（模拟 SELECT cnt, channel vs SELECT channel, cnt）
    swapped = [{"cnt": r["cnt"], "channel": r["channel"]} for r in ROWS]
    assert hash_rows(swapped) == hash_rows(ROWS)


def test_numeric_formatting_normalized() -> None:
    # 1.5 与 1.50、233851 与 233851.0 是同一答案
    assert hash_rows([{"v": 1.5}]) == hash_rows([{"v": 1.50}])
    assert hash_rows([{"v": 233851}]) == hash_rows([{"v": 233851.0}])


async def test_stored_gold_hash_matches_fresh_computation() -> None:
    # 端到端防漂移：库里 id=1 的金标哈希必须与当前代码重算的一致。
    # 归一化逻辑改版后若忘了重建评测集，所有执行类会静默全挂——这条测试守住它。
    async with admin_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT gold_sql, result_hash FROM eval.queries WHERE id = 1"
        )
        assert row is not None, "eval.queries 为空——先跑 eval/build_eval_set.py"
        fresh = hash_rows(await conn.fetch(row["gold_sql"]))
        assert fresh == row["result_hash"]
