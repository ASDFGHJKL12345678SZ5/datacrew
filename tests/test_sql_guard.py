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


class TestGate5SubqueryAndCase:
    """最终审查发现的两个闸5缺陷的回归（2026-10-01）：

    1. 子查询/CTE 内的 SELECT * 曾绕过顶层投影检查；
    2. 未加引号的大写标识符被误杀（PG 会折叠成小写，查询本合法）。
    """

    def test_star_in_subquery_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="禁止 SELECT"):
            validate_sql("SELECT id FROM (SELECT * FROM users) t", ALLOWED)

    def test_star_in_cte_rejected(self) -> None:
        with pytest.raises(UnsafeSQLError, match="禁止 SELECT"):
            validate_sql(
                "WITH t AS (SELECT * FROM users) SELECT id FROM t", ALLOWED
            )

    def test_uppercase_unquoted_table_ok(self) -> None:
        """PG 折叠未加引号标识符：FROM USERS ≡ FROM users，必须放行。"""
        r = validate_sql("SELECT id FROM USERS WHERE id = 1", ALLOWED)
        assert r.tables == ["users"]

    def test_uppercase_unquoted_column_ok(self) -> None:
        r = validate_sql("SELECT ID, Name FROM Users", ALLOWED)
        assert r.tables == ["users"]

    def test_quoted_mixed_case_table_rejected(self) -> None:
        """加引号的 "Users" 在 PG 里是另一个对象，registry 没有就该拒。"""
        with pytest.raises(UnsafeSQLError, match="未授权的表"):
            validate_sql('SELECT id FROM "Users"', ALLOWED)

    def test_count_star_still_ok(self) -> None:
        """COUNT(*) 在聚合内部不是投影，全层检查也不能误杀。"""
        r = validate_sql("SELECT COUNT(*) FROM users", ALLOWED)
        assert not r.needs_approval

class TestGate6FunctionWhitelist:
    """最终审查新增：函数接入从黑名单改为白名单（2026-10-01）。

    攻击样本全部来自真实红队实测：query_to_xml 一句话绕过全部七道闸、
    倒出 838KB 全表 XML；pg_terminate_backend 杀掉 RO 连接池。
    黑名单永远追不完——写黑名单时根本想不到这两种攻击。
    """

    def test_query_to_xml_blocked(self) -> None:
        """query_to_xml 把 SQL 当字符串参数执行，表/列白名单全部失效。"""
        with pytest.raises(UnsafeSQLError, match="危险函数: query_to_xml"):
            validate_sql(
                "SELECT query_to_xml('SELECT id,name,city FROM biz.users', true, false, '')",
                ALLOWED,
            )

    def test_query_to_xml_schema_variant_blocked(self) -> None:
        with pytest.raises(UnsafeSQLError, match="query_to_xml_and_xmlschema"):
            validate_sql(
                "SELECT query_to_xml_and_xmlschema('SELECT 1', true, false, '')",
                ALLOWED,
            )

    def test_pg_terminate_backend_blocked(self) -> None:
        """杀应用自己的连接池 = DoS（RO 角色实测可执行）。"""
        with pytest.raises(UnsafeSQLError, match="pg_terminate_backend"):
            validate_sql("SELECT pg_terminate_backend(42)", ALLOWED)

    def test_pg_cancel_backend_blocked(self) -> None:
        with pytest.raises(UnsafeSQLError, match="pg_cancel_backend"):
            validate_sql("SELECT pg_cancel_backend(pg_backend_pid())", ALLOWED)

    def test_array_agg_blocked_by_whitelist(self) -> None:
        """聚合行数有界但单行体积无界——收集类函数不进白名单。"""
        with pytest.raises(UnsafeSQLError, match="未授权的函数: array_agg"):
            validate_sql("SELECT array_agg(id) FROM users", ALLOWED)

    def test_string_agg_blocked_by_whitelist(self) -> None:
        with pytest.raises(UnsafeSQLError, match="未授权的函数"):
            validate_sql("SELECT string_agg(id::text, ',') FROM users", ALLOWED)

    def test_unknown_function_blocked(self) -> None:
        with pytest.raises(UnsafeSQLError, match="未授权的函数: my_custom_fn"):
            validate_sql("SELECT my_custom_fn(id) FROM users", ALLOWED)

    def test_and_or_not_treated_as_language_not_function(self) -> None:
        """AND/OR 在 sqlglot 里也是 Func 子类——不能被当成函数毙掉。"""
        r = validate_sql(
            "SELECT id FROM users WHERE id > 1 AND name IS NOT NULL OR id < 0",
            ALLOWED,
        )
        assert r.tables == ["users"]

    def test_cast_and_case_not_treated_as_function(self) -> None:
        r = validate_sql(
            "SELECT CASE WHEN pay_amount > 100 THEN 'big' ELSE 'small' END AS tier "
            "FROM orders WHERE created_at::date = current_date",
            ALLOWED,
        )
        assert r.tables == ["orders"]

    def test_count_star_survives_whitelist(self) -> None:
        r = validate_sql("SELECT COUNT(*) FROM users", ALLOWED)
        assert not r.needs_approval


class TestGate4CteShadowing:
    """最终审查新增：CTE 别名不能遮蔽 CTE 体内的真实表（2026-10-01）。"""

    def test_cte_shadowing_attack_blocked(self) -> None:
        """WITH queries AS (SELECT count(*) FROM eval.queries) ——
        CTE 别名 queries 曾把真实表 eval.queries 一起减掉，白名单失效。"""
        with pytest.raises(UnsafeSQLError, match="未授权的表"):
            validate_sql(
                "WITH queries AS (SELECT count(*) FROM eval.queries) "
                "SELECT count(*) FROM queries",
                ALLOWED,
            )

    def test_cte_shadowing_information_schema_blocked(self) -> None:
        with pytest.raises(UnsafeSQLError, match="未授权的表"):
            validate_sql(
                "WITH tables AS (SELECT count(*) FROM information_schema.tables) "
                "SELECT count(*) FROM tables",
                ALLOWED,
            )

    def test_legit_cte_still_works(self) -> None:
        r = validate_sql(
            "WITH paid AS (SELECT user_id FROM orders WHERE order_status = 'paid') "
            "SELECT user_id FROM paid",
            ALLOWED,
        )
        assert r.tables == ["orders"]

    def test_recursive_cte_self_reference_ok(self) -> None:
        """CTE 体内对自身别名的无限定引用是合法递归，不能当未授权表。"""
        r = validate_sql(
            "WITH RECURSIVE t(n) AS ("
            "SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 10) "
            "SELECT count(*) FROM t",
            ALLOWED,
        )
        assert r.tables == []

    def test_lateral_alias_column_ok(self) -> None:
        r = validate_sql(
            "SELECT g FROM users, LATERAL (SELECT 1 AS g) x", ALLOWED
        )
        assert r.tables == ["users"]


class TestGate3SetOperations:
    """最终审查新增：纯 SELECT 的集合运算应放行（曾一律误杀且话术误导）。"""

    def test_union_all_allowed(self) -> None:
        r = validate_sql(
            "SELECT id FROM users UNION ALL SELECT user_id FROM orders", ALLOWED
        )
        assert set(r.tables) == {"users", "orders"}

    def test_except_allowed(self) -> None:
        r = validate_sql(
            "SELECT id FROM users EXCEPT SELECT user_id FROM orders", ALLOWED
        )
        assert set(r.tables) == {"users", "orders"}

    def test_union_with_write_branch_rejected(self) -> None:
        """分支里藏写操作照样拒（集合运算不是免死金牌）。

        注：这种混合语法 sqlglot 直接解析失败——拒绝发生在更早的闸1，
        同样是拒，且话术对自养同样有效。
        """
        with pytest.raises(UnsafeSQLError):
            validate_sql(
                "SELECT id FROM users UNION ALL DELETE FROM users", ALLOWED
            )


class TestGate7TrivialWhere:
    """最终审查新增：同义 WHERE 骗不过大表审批（2026-10-01）。"""

    def test_where_1_eq_1_needs_approval(self) -> None:
        r = validate_sql("SELECT id FROM orders WHERE 1=1", ALLOWED)
        assert r.needs_approval

    def test_where_true_needs_approval(self) -> None:
        r = validate_sql("SELECT id FROM orders WHERE TRUE", ALLOWED)
        assert r.needs_approval

    def test_real_where_skips_approval(self) -> None:
        r = validate_sql(
            "SELECT id FROM orders WHERE created_at >= '2026-01-01'", ALLOWED
        )
        assert not r.needs_approval

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
