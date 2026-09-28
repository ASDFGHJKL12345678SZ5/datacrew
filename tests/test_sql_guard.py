"""SQL 安全闸单元测试：每一道闸配攻击用例。

这些测试是简历上"七道防注入闸"的直接证据 —— 每次 CI 全绿 = 每道闸都已验证。
"""
from __future__ import annotations

import pytest

from app.tools.sql_guard import (
    UnsafeSQLError,
    validate_sql,
)

# schema 注册表（模拟 DB 内省结果）
ALLOWED = {
    "users": {"id", "name", "city", "channel_source", "created_at"},
    "orders": {"id", "user_id", "channel", "gmv_amount", "pay_amount",
               "order_status", "created_at", "pay_time"},
    "products": {"id", "name", "category", "status", "list_price"},
    "order_items": {"id", "order_id", "product_id", "quantity", "price"},
    "traffic_logs": {"id", "user_id", "event_type", "ts"},
}


class TestGate1Parseable:
    def test_empty_sql_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="为空"):
            validate_sql("", ALLOWED)

    def test_garbage_sql_rejected(self) -> None:
        """sqlglot 宽容解析会把纯文本解析成 Column —— 闸3 会拦住它。

        这正是纵深防御的意义：不依赖任何单道闸的完备性。
        """
        with pytest.raises(UnsafeSQLError, match="仅允许 SELECT"):
            validate_sql("这是一个不是SQL的字符串", ALLOWED)


class TestGate2SingleStatement:
    def test_multi_statement_injection_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="单条语句"):
            validate_sql("SELECT id FROM users; DROP TABLE users;", ALLOWED)

    def test_trailing_semicolon_ok(self) -> None:
        r = validate_sql("SELECT id FROM users;", ALLOWED)
        assert r.tables == ["users"]


class TestGate3ReadOnly:
    @pytest.mark.parametrize("sql", [
        "DELETE FROM users WHERE id = 1",
        "UPDATE orders SET pay_amount = 0",
        "INSERT INTO users (name, channel_source) VALUES ('x', 'app')",
        "DROP TABLE users",
        "TRUNCATE users",
        "ALTER TABLE users ADD COLUMN x int",
        "COPY users TO '/tmp/x.csv'",
    ])
    def test_write_statements_rejected(self, sql: str) -> None:
        with pytest.raises(UnsafeSQLError, match="仅允许 SELECT"):
            validate_sql(sql, ALLOWED)

    def test_case_obfuscated_drop_rejected(self) -> None:
        """大小写混淆绕过尝试：AST 层面识别，与大小写无关。"""
        with pytest.raises(UnsafeSQLError):
            validate_sql("dRoP tAbLe users", ALLOWED)

    def test_cte_allowed(self) -> None:
        r = validate_sql(
            "WITH paid AS (SELECT user_id FROM biz.orders WHERE order_status = 'paid') "
            "SELECT user_id FROM paid",
            ALLOWED,
        )
        assert "paid" in r.tables or "orders" in r.tables


class TestGate4TableWhitelist:
    def test_unknown_table_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="未授权的表"):
            validate_sql("SELECT id FROM secrets", ALLOWED)

    def test_schema_qualified_table_ok(self) -> None:
        """LLM 常生成 biz.orders 这种带 schema 的表名，应能通过。"""
        r = validate_sql("SELECT id FROM biz.orders WHERE order_status = 'paid'", ALLOWED)
        assert r.tables == ["orders"]


class TestGate5ColumnWhitelist:
    def test_select_star_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="禁止 SELECT"):
            validate_sql("SELECT * FROM users", ALLOWED)

    def test_unknown_column_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="未授权的列"):
            validate_sql("SELECT password FROM users", ALLOWED)

    def test_count_star_ok(self) -> None:
        """COUNT(*) 是聚合不是 SELECT *，应放行。"""
        r = validate_sql("SELECT COUNT(*) FROM users", ALLOWED)
        assert not r.needs_approval


class TestGate6DangerousFunctions:
    def test_pg_sleep_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="危险函数"):
            validate_sql("SELECT id FROM users WHERE pg_sleep(10) IS NULL", ALLOWED)

    def test_file_read_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="危险函数"):
            validate_sql("SELECT pg_read_file('/etc/passwd')", ALLOWED)

    def test_deep_subquery_rejected(self) -> None:
        sql = (
            "SELECT id FROM users WHERE id IN ("
            "SELECT user_id FROM orders WHERE id IN ("
            "SELECT order_id FROM order_items WHERE product_id IN ("
            "SELECT id FROM products WHERE id IN ("
            "SELECT id FROM products WHERE id < 10))))"
        )
        with pytest.raises(UnsafeSQLError, match="嵌套深度"):
            validate_sql(sql, ALLOWED)


class TestGate7BigTableRules:
    def test_big_table_no_where_needs_approval(self) -> None:
        r = validate_sql("SELECT id, pay_amount FROM orders", ALLOWED)
        assert r.needs_approval
        assert r.approval_reason is not None

    def test_approved_big_scan_still_limited(self) -> None:
        """审批通过 ≠ 放弃行数上限：纵深防御不允许审批削弱保护。"""
        r = validate_sql("SELECT id, pay_amount FROM orders", ALLOWED)
        assert r.needs_approval
        assert "LIMIT 1000" in r.sql  # 即使获批，也自动包裹 LIMIT

    def test_big_table_with_where_ok(self) -> None:
        r = validate_sql(
            "SELECT id, pay_amount FROM orders WHERE pay_time >= '2026-01-01'", ALLOWED
        )
        assert not r.needs_approval

    def test_aggregate_not_wrapped(self) -> None:
        """聚合查询结果行数有界，不强制包裹 LIMIT。"""
        r = validate_sql(
            "SELECT channel, SUM(pay_amount) AS s FROM orders GROUP BY channel", ALLOWED
        )
        assert not r.wrapped_limit
        assert "LIMIT" not in r.sql.upper()

    def test_small_table_auto_limit(self) -> None:
        r = validate_sql("SELECT name FROM users", ALLOWED)
        assert r.wrapped_limit
        assert "LIMIT 1000" in r.sql

    def test_explicit_limit_preserved(self) -> None:
        r = validate_sql("SELECT name FROM users LIMIT 10", ALLOWED)
        assert not r.wrapped_limit


class TestEndToEnd:
    def test_typical_business_query(self) -> None:
        """真实业务问法：上个月各渠道实付销售额。"""
        r = validate_sql(
            "SELECT channel, SUM(pay_amount) AS pay_total FROM biz.orders "
            "WHERE order_status IN ('paid', 'refunded') "
            "AND pay_time >= '2026-08-01' AND pay_time < '2026-09-01' GROUP BY channel",
            ALLOWED,
        )
        assert r.tables == ["orders"]
        assert not r.needs_approval
