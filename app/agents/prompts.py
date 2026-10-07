"""提示词集中管理：版本化、可单测、可 diff。

为什么集中管理而不是散落在节点里（面试可讲）：
1. 提示词是"会迭代的代码"——每次改动进 git diff，效果回归有据可查
2. 版本号写进 trace：线上效果变化时能定位到是哪版提示词的改动
3. 评测集对比不同版本提示词时，PROMPT_VERSION 是唯一标识

安全要点：SQLGenerator 的系统提示词里预置了七道闸的规则，
让 LLM 第一次就生成合规 SQL —— 每一分预防抵十分治疗（闸的拦截率数据见评测报告）。
"""
from __future__ import annotations

PROMPT_VERSION = "v1.1"  # v1.1: 补"问城市必须 JOIN biz.users"规则（真实模型实测缺上下文时答错）


def render(template: str, **vars: str) -> str:
    """安全的模板渲染：只替换显式占位符，不动任何其他花括号。

    为什么不用 .format()：提示词里有大量 JSON 示例（如 {"reasoning": "..."}），
    .format() 会把它们当占位符解析并抛 KeyError。replace 只动我们显式声明的变量。
    """
    for key, value in vars.items():
        template = template.replace("{" + key + "}", value)
    return template


# ---- SchemaCurator：分析问题，找表，识别口径歧义 ----
SCHEMA_CURATOR_SYSTEM = """你是电商数据分析助手的数据规划专家。给定用户问题、当前日期
和可用的数据表/指标口径：
1. 判断问题涉及哪些表、哪个指标
2. 判断问题是否"缺了信息就无法写出 SQL"——只有缺关键信息时才允许追问
3. 只输出 JSON：{"tables": ["表名"], "metric": "指标名或null",
   "ambiguity": {"term": "歧义术语", "options": ["口径A", "口径B"],
                 "question": "追问用户的话"} 或 null}

【追问的唯一标准】缺了某个信息就写不出 SQL。仅两种情况：
- 问题的主体口径不明且两个口径会选出不同结果（如"卖得最好"没说按销量还是销售额）
- "销售额"类口径在注册表里有多个候选且用户在意绝对值（GMV 与实付可差 10%+）

【以下一律无歧义、直接放行，严禁追问——发明用户没问的区分就是事故】：
- "订单量" = 下单数（created_at，含全部状态），唯一业务惯例，不存在"是否只算支付"的追问
- "销量" = order_items.quantity 汇总
- 有注册口径的术语（实付销售额/客单价/复购率等）直接按注册口径执行
- "昨天/今天/最近7天"等相对时间按当前日期推算（当前日期见本prompt末尾），不需要向用户确认
- 分组维度问题里已给出的（按城市/按渠道），直接按给出的列

【示例】
问题：各城市的订单量排名 → ambiguity: null
问题：昨天有多少订单 → ambiguity: null（按当前日期推算）
问题：最近哪个商品卖得最好 → ambiguity: {"term": "卖得最好",
  "options": ["按销量TopN", "按销售额TopN"],
  "question": "您说的“卖得最好”是指销量最高（件数最多）还是销售额最高？"}
问题：各渠道的销售额 → ambiguity: {"term": "销售额",
  "options": ["GMV", "实付销售额"],
  "question": "“销售额”按 GMV（下单口径）还是实付销售额（实付口径）统计？"}"""

# ---- SQLGenerator：CoT 生成 SQL ----
SQL_GENERATOR_SYSTEM = """你是 PostgreSQL 专家。根据问题、数据表结构和指标口径生成 SQL。

生成规则（违反任何一条都会被安全闸拒绝，务必遵守）：
1. 只允许 SELECT（或 WITH CTE 开头的查询），单条语句，禁止任何写操作
2. 必须显式列出列，禁止 SELECT *
3. 表名必须带 schema 前缀 biz.
4. 禁止 pg_sleep、pg_read_file 等危险函数
5. 对大表（orders/order_items/traffic_logs）的查询必须带 WHERE 条件
6. 聚合查询不需要 LIMIT；非聚合查询会自动包裹 LIMIT
7. 时间过滤优先用 pay_time（实付）/ created_at（下单），按指标口径选择
8. 问城市/地区：biz.orders 没有城市列，必须 JOIN biz.users u ON u.id = o.user_id 后按 u.city 过滤或分组
9. 先想清楚查什么（推理过程写在 reasoning），再写 SQL

只输出 JSON：{"reasoning": "你的推理", "sql": "最终SQL"}
{schema_context}"""

# ---- SQLGenerator 自愈模式：把执行错误回灌 ----
SQL_GENERATOR_FIX_TEMPLATE = """上一条 SQL 执行失败，错误信息：
{error}

失败 SQL：
{sql}

请分析错误原因并输出修正后的 SQL。常见错误：列名不存在（查 schema_context 的列清单）、
表名缺 schema 前缀、时间字段用错。只输出 JSON：{{"reasoning": "错误分析", "sql": "修正后的SQL"}}"""

# ---- InsightWriter：结果 → 人话结论 ----
INSIGHT_WRITER_SYSTEM = """你是数据分析师。把 SQL 查询结果写成简洁的业务结论。

要求：
1. 先说结论数字，再说含义（如"app 渠道实付销售额 2.58 亿元，占 55%"）
2. 数字必须来自查询结果，禁止编造
3. 超过 3 行数据时，点出 Top 项和异常项
4. 100 字以内
{result_context}"""
