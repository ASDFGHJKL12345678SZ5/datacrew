"""SQL 安全策略：函数白/黑名单、大表清单等业务规则（领域层，可单测）。

为什么策略常量放 domain 层：这是"业务决策"（哪些函数对这个问数系统是合法的），
不是技术细节。安全闸 sql_guard.py 只负责"怎么查"，这里决定"查什么算合法"。

设计哲学（最终审查后的修订）：
    黑名单永远追不完。query_to_xml() 能把任意 SQL 当字符串参数传进去执行，
    pg_terminate_backend() 能杀应用自己的连接池——它们都不在任何黑名单上，
    因为写黑名单时根本想不到。所以函数准入改为白名单：只放行这个系统
    确实需要的函数，其余一律拒绝。新函数要用？显式加进来并想清楚理由。
"""
from __future__ import annotations

# 硬黑名单：即使将来误加进白名单也要拦的（纵深防御第二层，明确的拒绝消息）
HARD_BLOCK_FUNCTIONS = {
    # 睡眠/资源攻击
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    # 文件系统读取
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export",
    # 服务端任意连接/执行（query_to_xml 一家：SQL 当字符串参数执行，
    # 一句话绕过表/列白名单——实测能倒出全表 838KB XML）
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
    "dblink", "dblink_exec", "postgres_fdw",
    # 连接/会话级破坏（杀应用自己的连接池 = DoS）
    "pg_terminate_backend", "pg_cancel_backend", "pg_terminate_all_backends",
    # 配置改写
    "set_config", "current_setting", "set_config_by_name",
}

# 函数白名单：只放行问数场景确实需要的（聚合/标量/日期/字符串，全部只读无副作用）
ALLOWED_FUNCTIONS = {
    # 聚合（行数有界；数组/字符串收集类刻意不收，单行体积无界）
    "count", "sum", "avg", "min", "max", "stddev", "stddev_pop",
    "stddev_samp", "variance", "var_pop", "var_samp",
    # 算术/标量
    "round", "floor", "ceil", "ceiling", "abs", "mod", "power", "sqrt",
    "sign", "trunc", "exp", "ln", "log",
    # 条件
    "coalesce", "nullif", "greatest", "least",
    # 日期时间
    "date_trunc", "date_part", "extract", "to_char", "to_date",
    "to_timestamp", "now", "current_date", "current_time",
    "current_timestamp", "localtimestamp", "localtime", "age", "epoch",
    # 字符串
    "lower", "upper", "initcap", "length", "char_length",
    "character_length", "trim", "ltrim", "rtrim", "concat", "concat_ws",
    "substring", "substr", "left", "right", "split_part", "replace",
    "position", "strpos", "lpad", "rpad", "format", "md5",
}

# 大表：无过滤条件的查询必须人工审批（防全表扫描/拖库）
BIG_TABLES = {"orders", "order_items", "traffic_logs"}

# 同义过滤：这些 WHERE 等价于"没有过滤"，骗不过大表审批
TRIVIAL_WHERE_PATTERNS = (
    "true",
    "1 = 1",
    "1=1",
)

MAX_SUBQUERY_DEPTH = 3
DEFAULT_ROW_LIMIT = 1000