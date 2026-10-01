"""SQL 安全闸：LLM 生成的 SQL 执行前的七道检查。

威胁模型（面试必讲）：
    LLM 可能被 prompt injection 诱导、或自己幻觉出危险 SQL：删表、拖库、
    超时查询、用注释/大小写/编码绕过关键字黑名单。
    本模块的设计哲学：**黑名单（关键词过滤）是最后手段，AST + 白名单才是根本**——
    关键字黑名单的攻击面太大（大小写、注释、Unicode 全角、URL 编码都能绕过）。
    最终审查再补一刀：**函数准入也从黑名单改成白名单**——query_to_xml() 把 SQL
    当字符串参数执行、pg_terminate_backend() 杀连接池，这类攻击写黑名单时
    根本想不到；白名单（只放行确实需要的函数）才追得完。

七道闸：
    闸1 可解析    ：sqlglot 解析成 AST，失败即拒（无法解析 = 无法审计 = 不放行）
    闸2 单语句    ：只允许一条语句，防 "SELECT 1; DROP TABLE users;"
    闸3 只读      ：SELECT / WITH CTE / 纯 SELECT 的集合运算（UNION 等）
    闸4 表白名单  ：表必须在 schema 注册表内（CTE 别名只遮蔽"外层引用"，
                   CTE 体内的真实表照查）
    闸5 列白名单  ：显式列必须在注册列内（含本查询派生的别名）；禁止 SELECT *
    闸6 函数白名单：只放行 domain.sql_policy.ALLOWED_FUNCTIONS 内的函数，
                   硬黑名单优先拦；子查询嵌套 ≤3
    闸7 大表规则  ：大表（orders/order_items/traffic_logs）无"有效" WHERE
                   （同义 TRUE/1=1 不算）→ 转人工审批；其余自动包裹 LIMIT 兜底

执行侧还有最后防线（见 sql_execute.py）：只读事务 + statement_timeout + 行数上限。
即使前七关全部被绕过，数据库层还有第三层防护（datacrew_ro 角色本身无写权限）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.domain.sql_policy import (
    ALLOWED_FUNCTIONS,
    BIG_TABLES,
    DEFAULT_ROW_LIMIT,
    HARD_BLOCK_FUNCTIONS,
    MAX_SUBQUERY_DEPTH,
    TRIVIAL_WHERE_PATTERNS,
)

# sqlglot 里"是 Func 子类但不是用户函数调用"的语言构件（版本容错构造）：
# CAST/CASE(exp.If)/AND/OR/XOR/EXISTS/COLLATE/APPLY/TRY_CAST。
# 注意不能用 exp.Condition 一把筛——它在 sqlglot 30 是 Func 的基类，
# 会把 query_to_xml 这类 Anonymous 真危险函数一起漏掉（实测踩坑）。
_LANGUAGE_CONSTRUCTS = tuple(
    getattr(exp, n)
    for n in (
        "Cast", "Case", "If", "And", "Or", "Xor",
        "Exists", "Collate", "Apply", "TryCast",
    )
    if hasattr(exp, n)
)


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


def _is_read_only_select(node: exp.Expression) -> bool:
    """节点是纯 SELECT（集合运算递归下沉到每个分支，WITH CTE 也算）。"""
    if isinstance(node, exp.Select):
        return True
    if isinstance(node, exp.SetOperation):  # UNION / EXCEPT / INTERSECT
        return _is_read_only_select(node.this) and _is_read_only_select(node.expression)
    return False


def _has_aggregation(stmt: exp.Expression) -> bool:
    """是否有聚合（GROUP BY / 聚合函数）—— 聚合结果行数有界，无需 LIMIT 兜底。"""
    if stmt.args.get("group"):
        return True
    for node in stmt.walk():
        if isinstance(node, exp.AggFunc):
            return True
    return False


def _has_effective_where(stmt: exp.Expression) -> bool:
    """有"有效" WHERE：同义 TRUE / 1=1 不算过滤（骗不过大表审批）。"""
    where = stmt.args.get("where")
    if where is None:
        return False
    text = where.this.sql(comments=False).strip().strip("()").strip().lower()
    return text not in TRIVIAL_WHERE_PATTERNS


def _subquery_depth(stmt: exp.Expression, depth: int = 0) -> int:
    """计算最大子查询嵌套深度。"""
    deepest = depth
    for node in stmt.walk():
        if isinstance(node, exp.Select) and node is not stmt:
            deepest = max(deepest, _subquery_depth(node, depth + 1))
    return deepest


def _ident_key(node: exp.Expression | None) -> str:
    """PG 语义的标识符键：未加引号 -> 折叠小写；加引号 -> 原样。

    PostgreSQL 会把未加引号的标识符折叠成小写（FROM ORDERS 等同于 from orders），
    而 sqlglot 保留原始大小写。不做这层归一会把 LLM 生成的大写表名/列名误判
    为"未授权"（真实模型写 SELECT Amount FROM Biz.ORDERS 会被闸5误杀，逼出
    无意义的自愈轮）。加引号的标识符 PG 区分大小写，registry 里没有就该拒
    （"Orders" 与 orders 是两个对象）——两种语义都要忠于 PG。
    """
    if isinstance(node, exp.Identifier):
        return node.name if node.quoted else node.name.casefold()
    return (getattr(node, "name", "") or "").casefold()


def _cte_names(stmt: exp.Expression) -> set[str]:
    """所有 CTE 别名（WITH xxx AS ... 的临时名，不参与表白名单检查）。"""
    return {_ident_key(cte.args.get("alias"))
            for w in stmt.find_all(exp.With) for cte in w.expressions}


def _referenced_tables(stmt: exp.Expression) -> list[str]:
    """查询引用的真实表（剔除 CTE 别名对外层引用的遮蔽）。

    关键细节：只有"CTE 体外层的 FROM 引用"才被别名遮蔽——CTE 体内的表是
    真实表，照查。早期版对整个集合做差集，
    "WITH queries AS (SELECT count(*) FROM eval.queries)
     SELECT count(*) FROM queries" 里 eval.queries 会被 queries 别名一起减掉，
    表白名单整体失效（CTE 别名遮蔽漏洞）。
    """
    cte_names = _cte_names(stmt)
    out: set[str] = set()
    for t in stmt.find_all(exp.Table):
        name = _ident_key(t.this)
        inside_cte = t.find_ancestor(exp.CTE) is not None
        schema_qualified = t.args.get("db") is not None or t.args.get("catalog") is not None
        if name not in cte_names:
            out.add(name)  # 普通真实表
        elif not inside_cte and not schema_qualified:
            continue  # 外层无限定引用 = 对 CTE 别名的引用（合法遮蔽）
        elif inside_cte and not schema_qualified:
            continue  # CTE 体内无限定自引用（WITH RECURSIVE t ... FROM t）
        else:
            # 带 schema 限定（eval.queries）：即使撞 CTE 别名也是真实表——
            # PG 的解析规则：WITH 别名没有 schema，限定名优先解析到真表。
            # 没有这条，CTE 别名遮蔽攻击（WITH queries AS (SELECT ...
            # FROM eval.queries) ...）会把表白名单整个绕过去。
            out.add(name)
    return sorted(out)


def _derived_column_names(stmt: exp.Expression) -> set[str]:
    """本查询派生的列名：CTE 声明的输出列 + 各层 SELECT 的输出别名。

    这些名字由查询自己产生（WITH RECURSIVE t(n) ...、LATERAL (SELECT 1 AS g)），
    不是泄露的真实列——早期版一律按"未授权"拒掉，把合法递归 CTE 误杀，
    错误话术还误导自愈（"未授权的列: n"）。
    """
    derived: set[str] = set()
    for cte in stmt.find_all(exp.CTE):
        # WITH t(n) AS ...：列声明挂在 alias 节点（TableAlias.columns），
        # 不在 CTE 的直接 args 里（sqlglot 30.20 实测）
        alias = cte.args.get("alias")
        for col in getattr(alias, "columns", None) or []:
            derived.add(_ident_key(col))
    for select in stmt.find_all(exp.Select):
        for proj in select.expressions:
            if isinstance(proj, exp.Alias):
                derived.add(_ident_key(proj.args.get("alias")))
    return derived


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

    # 闸3：只读（SELECT / WITH CTE / 纯 SELECT 的集合运算）
    if not _is_read_only_select(stmt):
        raise UnsafeSQLError(
            f"仅允许 SELECT 查询，检测到 {type(stmt).__name__}（写操作/DDL 一律拒绝）"
        )

    # 闸4：表白名单（CTE 别名只遮蔽外层引用，CTE 体内的真实表照查）
    tables = _referenced_tables(stmt)
    unknown = [t for t in tables if t not in allowed_tables]
    if unknown:
        raise UnsafeSQLError(
            f"未授权的表: {unknown}（可用表: {sorted(allowed_tables)}）"
        )

    # 闸5：列白名单 + 禁 SELECT *
    # 注意：只禁直接投影的 Star（SELECT * / SELECT t.*）；
    #       COUNT(*) 这类聚合函数内部的 Star 合法且必要，放行。
    # Star 检查覆盖所有 SELECT 层（含子查询/CTE）："SELECT id FROM
    #       (SELECT * FROM orders) t" 会把内层星号漏掉——列暴露面与顶层相同。
    for select in stmt.find_all(exp.Select):
        for proj in select.expressions:
            if isinstance(proj, exp.Star):
                raise UnsafeSQLError("禁止 SELECT *，请显式列出所需列（防止隐式全列拖取）")
            if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                raise UnsafeSQLError("禁止 SELECT t.*，请显式列出所需列")
    allowed_cols: set[str] = set()
    for t in tables:
        allowed_cols |= allowed_tables[t]
    allowed_cols |= _derived_column_names(stmt)
    for col in stmt.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue  # t.* 已在上面按 Star 拒绝
        key = _ident_key(col.this)
        if key not in allowed_cols:
            raise UnsafeSQLError(
                f"未授权的列: {key}（表 {tables} 的可用列见 schema 注册表）"
            )

    # 闸6：函数白名单（硬黑名单优先）+ 嵌套深度
    # 注意：pg_sleep/query_to_xml 等 PostgreSQL 专有函数在 sqlglot 中是
    #       Anonymous 节点，sql_name() 返回 "ANONYMOUS"，真实函数名在
    #       func.name 里；类型化节点（Count/Sum/ArrayAgg...）用 sql_name()。
    #       两个名字都查，白名单之外一律拒。
    for func in stmt.find_all(exp.Func):
        # 语言构件跳过表：CAST/CASE/AND/OR/XOR/EXISTS 在 sqlglot 里也是 Func
        # 子类，但它们不是用户函数调用。注意不能用 exp.Condition 一把筛——
        # 它在 sqlglot 30 是 Func 的基类，会把 query_to_xml 这类 Anonymous
        # 真危险函数一起漏掉（实测踩坑，攻击样本全部漏网）。
        if isinstance(func, _LANGUAGE_CONSTRUCTS):
            continue
        candidates = {
            n.lower()
            for n in (func.sql_name(), func.name or "")
            if n and n.upper() != "ANONYMOUS"
        }
        hit = candidates & HARD_BLOCK_FUNCTIONS
        if hit:
            raise UnsafeSQLError(f"禁用危险函数: {sorted(hit)[0]}")
        if not candidates & ALLOWED_FUNCTIONS:
            raise UnsafeSQLError(
                f"未授权的函数: {sorted(candidates)[0]}（不在问数函数白名单内，"
                "白名单见 app/domain/sql_policy.py）"
            )
    depth = _subquery_depth(stmt)
    if depth > MAX_SUBQUERY_DEPTH:
        raise UnsafeSQLError(f"子查询嵌套深度 {depth} 超过上限 {MAX_SUBQUERY_DEPTH}")

    # 闸7：大表规则 + LIMIT 兜底
    wrapped = False
    normalized = stmt.sql(dialect="postgres")
    has_limit = stmt.args.get("limit") is not None
    touches_big = any(t in BIG_TABLES for t in tables)
    filtered = _has_effective_where(stmt)
    aggregated = _has_aggregation(stmt)

    if touches_big and not filtered and not aggregated:
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

    if not has_limit and not aggregated:
        normalized = f"SELECT * FROM ({normalized}) AS __limited LIMIT {row_limit}"
        wrapped = True

    return ValidatedSQL(sql=normalized, tables=tables, wrapped_limit=wrapped)