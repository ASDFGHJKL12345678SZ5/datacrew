"""SQL 安全闸：LLM 生成的 SQL 执行前的七道检查。

威胁模型（面试必讲）：
    LLM 可能被 prompt injection 诱导、或自己幻觉出危险 SQL：删表、拖库、
    超时查询、用注释/大小写/编码绕过关键字黑名单。
    本模块的设计哲学：**黑名单（关键词过滤）是最后手段，AST + 白名单才是根本**——
    关键字黑名单的攻击面太大（大小写、注释、Unicode 全角、URL 编码都能绕过），
    而"解析成语法树后只放行白名单内的结构"没有已知绕过方式。

七道闸：
    闸1 可解析    ：sqlglot 解析成 AST，失败即拒（无法解析 = 无法审计 = 不放行）
    闸2 单语句    ：只允许一条语句，防 "SELECT 1; DROP TABLE users;"
    闸3 只读      ：必须是 SELECT（允许 WITH CTE），拒绝 DML/DDL
    闸4 表白名单  ：表必须在 schema 注册表内（防访问未授权表）
    闸5 列白名单  ：显式列必须在注册列内；禁止 SELECT *（防隐式拖全列）
    闸6 高危模式  ：禁用危险函数（pg_sleep/文件读取等）、子查询嵌套 ≤3
    闸7 大表规则  ：大表（orders/order_items/traffic_logs）无 WHERE 条件 → 转人工审批；
                   其余查询自动包裹 LIMIT 兜底

执行侧还有最后防线（见 sql_execute.py）：只读事务 + statement_timeout + 行数上限。
即使前七关全部被绕过，数据库层还有第三层防护（datacrew_ro 角色本身无写权限）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# 危险函数：睡眠攻击、文件读取、任意代码执行类
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "dblink", "dblink_exec",
    "set_config", "current_setting",
}

# 大表：无 WHERE 条件的查询必须人工审批（防全表扫描/拖库）
BIG_TABLES = {"orders", "order_items", "traffic_logs"}

MAX_SUBQUERY_DEPTH = 3
DEFAULT_ROW_LIMIT = 1000


class UnsafeSQLError(Exception):
    """SQL 未通过安全闸。message 会回灌给 Agent 驱动自愈。"""


@dataclass
class ValidatedSQL:
    """通过安全闸的 SQL（可能被加固过）。"""

    sql: str
    tables: list[str] = field(default_factory=list)
    wrapped_limit: bool = False
    needs_approval: bool = False
    approval_reason: str | None = None


def _has_aggregation(stmt: exp.Select) -> bool:
    """是否有聚合（GROUP BY / 聚合函数）—— 聚合结果行数有界，无需 LIMIT 兜底。"""
    if stmt.args.get("group"):
        return True
    for node in stmt.walk():
        if isinstance(node, exp.AggFunc):
            return True
    return False


def _has_where(stmt: exp.Select) -> bool:
    return stmt.args.get("where") is not None


def _subquery_depth(stmt: exp.Expression, depth: int = 0) -> int:
    """计算最大子查询嵌套深度。"""
    deepest = depth
    for node in stmt.walk():
        if isinstance(node, exp.Select) and node is not stmt:
            deepest = max(deepest, _subquery_depth(node, depth + 1))
    return deepest


def validate_sql(
    sql: str,
    allowed_tables: dict[str, set[str]],
    row_limit: int = DEFAULT_ROW_LIMIT,
) -> ValidatedSQL:
    """七道闸主入口。通过返回 ValidatedSQL；不通过抛 UnsafeSQLError。

    Args:
        sql: LLM 生成的原始 SQL
        allowed_tables: {表名: {列名集合}}，来自 schema 注册表（DB 内省 + Redis 缓存）
        row_limit: 结果行数上限
    """
    if not sql or not sql.strip():
        raise UnsafeSQLError("SQL 为空")

    # 闸1：可解析
    try:
        statements = sqlglot.parse(sql)
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"SQL 解析失败（无法审计即不放行）: {e}") from e
    if not statements:
        raise UnsafeSQLError("SQL 解析结果为空")

    # 闸2：单语句
    if len(statements) > 1:
        raise UnsafeSQLError(f"仅允许单条语句，检测到 {len(statements)} 条（疑似多语句注入）")

    stmt = statements[0]

    # 闸3：只读（SELECT 或以 WITH 开头的 CTE 查询）
    if not isinstance(stmt, exp.Select):
        raise UnsafeSQLError(
            f"仅允许 SELECT 查询，检测到 {type(stmt).__name__}（写操作/DDL 一律拒绝）"
        )

    # 闸4：表白名单（CTE 别名是查询内定义的临时名，不参与白名单检查）
    # 注意：不同 sqlglot 版本的 WITH 参数键不同（with / with_），用 find_all 最稳
    cte_names = {cte.alias_or_name for w in stmt.find_all(exp.With) for cte in w.expressions}
    tables = sorted({t.name for t in stmt.find_all(exp.Table)} - cte_names)
    unknown = [t for t in tables if t not in allowed_tables]
    if unknown:
        raise UnsafeSQLError(
            f"未授权的表: {unknown}（可用表: {sorted(allowed_tables)}）"
        )

    # 闸5：列白名单 + 禁 SELECT *
    # 注意：只禁直接投影的 Star（SELECT * / SELECT t.*）；
    #       COUNT(*) 这类聚合函数内部的 Star 合法且必要，放行。
    for proj in stmt.expressions:
        if isinstance(proj, exp.Star):
            raise UnsafeSQLError("禁止 SELECT *，请显式列出所需列（防止隐式全列拖取）")
        if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
            raise UnsafeSQLError("禁止 SELECT t.*，请显式列出所需列")
    allowed_cols: set[str] = set()
    for t in tables:
        allowed_cols |= allowed_tables[t]
    for col in stmt.find_all(exp.Column):
        if col.name not in allowed_cols:
            raise UnsafeSQLError(
                f"未授权的列: {col.name}（表 {tables} 的可用列见 schema 注册表）"
            )

    # 闸6：高危函数与嵌套深度
    # 注意：pg_sleep 等 PostgreSQL 专有函数在 sqlglot 中是 Anonymous 节点，
    #       sql_name() 返回 "ANONYMOUS"，真实函数名在 func.name 里，两个都要查。
    for func in stmt.find_all(exp.Func):
        candidates = {func.sql_name().lower(), (func.name or "").lower()}
        hit = candidates & FORBIDDEN_FUNCTIONS
        if hit:
            raise UnsafeSQLError(f"禁用危险函数: {sorted(hit)[0]}")
    depth = _subquery_depth(stmt)
    if depth > MAX_SUBQUERY_DEPTH:
        raise UnsafeSQLError(f"子查询嵌套深度 {depth} 超过上限 {MAX_SUBQUERY_DEPTH}")

    # 闸7：大表规则 + LIMIT 兜底
    wrapped = False
    normalized = stmt.sql(dialect="postgres")
    has_limit = stmt.args.get("limit") is not None
    touches_big = any(t in BIG_TABLES for t in tables)

    if touches_big and not _has_where(stmt) and not _has_aggregation(stmt):
        # 审批的语义是"允许扫描大表"，不是"允许把全表灌进 LLM 上下文"——
        # 行数上限是纵深防御的一部分，审批通过也不能削弱它。
        if not has_limit:
            normalized = f"SELECT * FROM ({normalized}) AS __limited LIMIT {row_limit}"
            wrapped = True
        return ValidatedSQL(
            sql=normalized,
            tables=tables,
            wrapped_limit=wrapped,
            needs_approval=True,
            approval_reason=f"对大表 {tables} 的无过滤条件查询，需人工审批（防全表扫描/拖库）",
        )

    if not has_limit and not _has_aggregation(stmt):
        normalized = f"SELECT * FROM ({normalized}) AS __limited LIMIT {row_limit}"
        wrapped = True

    return ValidatedSQL(sql=normalized, tables=tables, wrapped_limit=wrapped)
