-- ============================================================
-- 03_eval_tables.sql：评测集与回归记录
-- 安全要点：eval schema 不授予 datacrew_ro！
--   eval.queries 里有 gold_sql（标准答案），Agent 的 DB 身份一旦能读就等于作弊。
--   这是"数据层权限设计"的真实案例，面试可讲。
-- 变更记录：
--   - 唯一约束从 question 单列改为 (question, category) 复合：
--     "双11当天的订单量" 同属 simple_agg 和 time_range，按问题唯一会错误合并。
--   - 曾用单 $ 写 DO 块（语法错误、静默失败），CI 干净库实测暴露；
--     现已修正为 $$ 且 CREATE TABLE 直接用复合唯一。
-- ============================================================
CREATE SCHEMA IF NOT EXISTS eval;

CREATE TABLE IF NOT EXISTS eval.queries (
    id          SERIAL PRIMARY KEY,
    question    TEXT NOT NULL,
    category    VARCHAR(32) NOT NULL,
    gold_sql    TEXT NOT NULL,            -- 人工修订过的标准 SQL
    result_hash TEXT NOT NULL,            -- 执行结果集指纹：客观判分依据
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT eval_queries_question_category_key UNIQUE (question, category)
);

-- 老库升级：question-only 唯一（eval_queries_question_key）换成复合唯一。
-- 幂等：已有复合约束则什么都不做；question_key 不存在则跳过删除。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'eval.queries'::regclass
          AND conname = 'eval_queries_question_category_key'
    ) THEN
        -- 老库 question-only 唯一的两种历史命名：PG 自动名（queries_question_key）
        -- 与早期显式名（eval_queries_question_key）都要清，否则跨类别重复问题仍被挡
        ALTER TABLE eval.queries DROP CONSTRAINT IF EXISTS queries_question_key;
        ALTER TABLE eval.queries DROP CONSTRAINT IF EXISTS eval_queries_question_key;
        ALTER TABLE eval.queries
            ADD CONSTRAINT eval_queries_question_category_key UNIQUE (question, category);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS eval.runs (
    id             SERIAL PRIMARY KEY,
    run_at         TIMESTAMPTZ DEFAULT now(),
    git_commit     VARCHAR(64),
    prompt_version VARCHAR(64),
    model          VARCHAR(64),
    total          INT,
    passed         INT,
    accuracy       NUMERIC(5,3),
    p95_latency_ms INT,
    cost_yuan      NUMERIC(10,2),
    notes          TEXT
);

CREATE INDEX IF NOT EXISTS idx_evalruns_runat ON eval.runs(run_at DESC);
