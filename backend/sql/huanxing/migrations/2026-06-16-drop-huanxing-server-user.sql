-- =====================================================
-- CLEAN-6：退役 huanxing_server / huanxing_user 两表 + 相关代码
--
-- 背景：这两表是唤星 HASN 化之前的旧 sidecar「服务器/用户登记」机制
--   （Guardian Agent 注册服务器 + 用户随服务器分布的旧模型）。HASN 身份体系
--   （hasn_humans / hasn_agents / hasn_nodes + Owner/Agent JWT）落地后，该机制
--   已被完全取代：
--     - 桌面端（hasn-node）对这两表 / 相关 URL 零引用（user_sync/user_check/
--       servers/dashboard 全无消费方）；
--     - 云端外部消费方为空（两表代码自成一体、仅 app/huanxing 内部互引）。
--   福仔 2026-06-16 拍板退役这两表及其相关代码。
--
-- 代码侧（同版本删除，本迁移须在新代码部署后执行）：
--   - model/schema/crud/service：huanxing_server* / huanxing_user* 全删
--   - admin API：server.py / user.py / dashboard.py 全删
--   - agent API：server.py / dashboard.py / user_sync.py / user_check.py 全删
--   - router wiring：admin v1 仅留 analytics；agent 仅留 files / website
--   - 生成 SQL 工件：huanxing_tables.sql / huanxing_{server,user}_{menu,dict}.sql 全删
--
-- 保留（与这两表无关、独立存活）：
--   - admin/analytics.py + analytics_service.py（读 hasn_billing 计费/用量看板）
--   - agent|user file.py / website.py（S3 上传 / 网站部署）
--   - llm_newapi_user_mapping.huanxing_user_id 列（= sys_user.id，非本表外键）
--   - 共享字典 huanxing_status（可能被 huanxing 文档子域复用，不在 DB 删）
--   - huanxing 文档子域菜单（document/document-version/document-autosave，另案退役）
--
-- ⚠️ 生产执行须经福仔停机窗口授权（与其它 public schema 切换同一约定）。
--    本机 dev 库已先行执行验证。
-- 幂等：全部 IF EXISTS / 按稳定标识（perms/name/path）删除，可重复执行。
-- =====================================================

-- 1) 清理后台菜单（服务器/用户登记的菜单 + 按钮；按稳定标识删，避免硬编码 ID）
DO $$
DECLARE
    v_menu_ids INTEGER[];
BEGIN
    SELECT array_agg(id) INTO v_menu_ids
    FROM sys_menu
    WHERE perms LIKE 'huanxing:server:%'
       OR perms LIKE 'huanxing:user:%'
       OR (type = 1 AND name IN ('HuanxingServer', 'HuanxingUser'))
       OR (type = 1 AND path IN ('/huanxing/server', '/huanxing/user',
                                 '/huanxing/huanxing_server', '/huanxing/huanxing_user'));

    IF v_menu_ids IS NOT NULL THEN
        -- 先解除角色-菜单关联（若存在该关联表），再删菜单本身
        DELETE FROM sys_role_menu WHERE menu_id = ANY(v_menu_ids);
        DELETE FROM sys_menu WHERE id = ANY(v_menu_ids);
    END IF;
END $$;

-- 2) DROP 两表（huanxing_user 无指向 huanxing_server 的真实外键，顺序无关；均 IF EXISTS + CASCADE）
DROP TABLE IF EXISTS "public"."huanxing_user" CASCADE;
DROP TABLE IF EXISTS "public"."huanxing_server" CASCADE;
