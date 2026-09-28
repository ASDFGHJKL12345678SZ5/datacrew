-- ============================================================
-- 02_roles.sql：最小权限角色
-- datacrew_ro 是 Agent 执行 SQL 的唯一身份：只读、限 schema、无未来写权限
-- ============================================================
CREATE ROLE datacrew_ro LOGIN PASSWORD 'datacrew_ro_pwd';

GRANT CONNECT ON DATABASE ecommerce TO datacrew_ro;
GRANT USAGE ON SCHEMA biz TO datacrew_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA biz TO datacrew_ro;

-- 未来在 biz schema 新建的表也自动只读（但绝不给 eval schema —— 见 03）
ALTER DEFAULT PRIVILEGES IN SCHEMA biz GRANT SELECT ON TABLES TO datacrew_ro;
