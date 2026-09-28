-- ============================================================
-- 03_eval_tables.sql：评测集与回归记录
-- 安全要点：eval schema 不授予 datacrew_ro！
--   eval.queries 里有 gold_sql（标准答案），Agent 的 DB 身份一旦能读就等于作弊。
--   这是"数据层权限设计"的真实案例，面试可讲。
-- ============================================================
CREATE SCHEMA IF NOT EXISTS eval;

CREATE TABLE eval.queries (
    id          SERIAL PRIMARY KEY,
    question    TEXT NOT NULL,
    category    VARCHAR(32) NOT NULL,     -- simple / multi_hop / ambiguous / dirty / time_compare
    gold_sql    TEXT NOT NULL,            -- 人工修订过的标准 SQL
    result_hash TEXT NOT NULL,            -- 执行结果集指纹：客观判分依据
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE eval.runs (
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

CREATE INDEX idx_evalruns_runat ON eval.runs(run_at DESC);
