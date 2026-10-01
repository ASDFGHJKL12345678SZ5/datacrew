-- ============================================================
-- 02_roles.sql：最小权限角色
-- datacrew_ro 是 Agent 执行 SQL 的唯一身份：只读、限 schema、无未来写权限
-- 幂等：角色已存在则跳过创建（角色是集群级，DB 重建后仍在）
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datacrew_ro') THEN
        CREATE ROLE datacrew_ro LOGIN PASSWORD 'datacrew_ro_pwd';
    END IF;
END $$;

GRANT CONNECT ON DATABASE ecommerce TO datacrew_ro;
GRANT USAGE ON SCHEMA biz TO datacrew_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA biz TO datacrew_ro;

-- 未来在 biz schema 新建的表也自动只读（但绝不给 eval schema —— 见 03）
ALTER DEFAULT PRIVILEGES IN SCHEMA biz GRANT SELECT ON TABLES TO datacrew_ro;
