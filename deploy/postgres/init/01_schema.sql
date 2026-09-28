-- ============================================================
-- 01_schema.sql：Mock 电商业务库
-- 设计原则：结构真实 + 故意埋入三类"脏点"（用于验证 Agent 鲁棒性）
--   埋点A（口径歧义）：orders.gmv_amount vs pay_amount —— "销售额"到底指哪个？
--   埋点B（脏值）：products.status 历史数据混入 NULL / 2(预售)
--   埋点C（时间陷阱）：orders.created_at(下单) vs pay_time(支付，未支付为 NULL)
-- 这些埋点会进入评测集（ambiguous / dirty / time_compare 三类题目）
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector：指标口径知识检索用

CREATE SCHEMA IF NOT EXISTS biz;

CREATE TABLE biz.users (
    id             BIGSERIAL PRIMARY KEY,
    name           VARCHAR(64)  NOT NULL,
    city           VARCHAR(32),
    channel_source VARCHAR(16)  NOT NULL,           -- app / miniapp / h5
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE biz.products (
    id         BIGSERIAL PRIMARY KEY,
    name       VARCHAR(128) NOT NULL,
    category   VARCHAR(32)  NOT NULL,
    status     SMALLINT,                            -- 0下架 1上架 2预售；脏数据含 NULL
    list_price NUMERIC(12,2) NOT NULL CHECK (list_price >= 0)
);

CREATE TABLE biz.orders (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT        NOT NULL REFERENCES biz.users(id),
    channel      VARCHAR(16)   NOT NULL,
    gmv_amount   NUMERIC(14,2) NOT NULL,            -- GMV（含取消/退款单）
    pay_amount   NUMERIC(14,2) NOT NULL,            -- 实付金额
    order_status VARCHAR(16)   NOT NULL,            -- created/paid/cancelled/refunded
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),  -- 下单时间
    pay_time     TIMESTAMPTZ                            -- 支付时间（NULL = 未支付）
);

CREATE TABLE biz.order_items (
    id         BIGSERIAL PRIMARY KEY,
    order_id   BIGINT       NOT NULL REFERENCES biz.orders(id),
    product_id BIGINT       NOT NULL REFERENCES biz.products(id),
    quantity   INT          NOT NULL CHECK (quantity > 0),
    price      NUMERIC(12,2) NOT NULL               -- 成交单价
);

CREATE TABLE biz.traffic_logs (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT       REFERENCES biz.users(id),
    event_type VARCHAR(16)  NOT NULL,               -- view / click / cart / order
    ts         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 索引：让"查询性能"成为可讨论的真实问题
CREATE INDEX idx_orders_user_paytime ON biz.orders(user_id, pay_time);
CREATE INDEX idx_orders_channel_created ON biz.orders(channel, created_at);
CREATE INDEX idx_orders_status_created ON biz.orders(order_status, created_at);
CREATE INDEX idx_items_order ON biz.order_items(order_id);
CREATE INDEX idx_items_product ON biz.order_items(product_id);

-- 指标口径注册表：Agent 的"口径知识"，pgvector 检索对象（D2 灌 embedding）
CREATE TABLE biz.metric_definitions (
    id           SERIAL PRIMARY KEY,
    metric_name  VARCHAR(64) UNIQUE NOT NULL,       -- GMV / 实付销售额 / 客单价 / 复购率 ...
    definition   TEXT NOT NULL,
    sql_hint     TEXT,
    embedding    vector(1024)                       -- BGE-M3 输出维度 1024
);
