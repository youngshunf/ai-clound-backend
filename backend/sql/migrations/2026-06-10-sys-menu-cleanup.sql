-- =====================================================
-- 管理端 sys_menu 清理：修复 Deck 路由名冲突 + 删重复/死路由 + 重名标题/图标优化
-- =====================================================
-- 背景（登录崩溃）:
--   管理端登录报 "A route named 'Deck' has been added as a child of a route with
--   the same name. Route names must be unique..."（vue-router generateAccessible）。
--   根因 = sys_menu 中 Deck 目录(type=0,id≈1076)与其子页(type=1,id≈1077)同名 'Deck'，
--   且该子页组件 /deck/deck/index.vue 在前端根本不存在（死路由）。
--
-- 一并清理（福仔 2026-06-10 拍板「全部清掉 + 重名/图标一起优化」）:
--   ① 整目录死菜单/测试垃圾：Hermes / Lead 冒烟测试 / Hasn 社区 admin / Deck / Codegen 测试
--      —— 这些目录下所有页面的前端 .vue 组件均不存在，或为 codegen 测试脚手架。
--   ② HASN 目录下 22 个死页面（组件 .vue 不存在）及其按钮子菜单。
--   ③ 重复按钮：codegen 跑了两次，后端实际用「冒号格式」权限(hasn:x:y:add)，
--      残留的「下划线格式」(hasn:x_y:add)删之。
--   ④ 孤儿按钮：父页已删的悬空 user:subscription:* 按钮。
--   ⑤ 重名标题：把 codegen 占位的同名标题（App管理×8 / HASN管理×4 / Lead管理×4 /
--      用户管理×3 / 应用版本×2 / AI管理×2）改为各自语义化中文名。
--   ⑥ 图标/目录名：把 lucide:folder 默认图标 + 英文目录名换成中文名 + 语义图标。
--
-- 安全性: 删除的菜单均未被任何角色(sys_role_menu)引用（超管 is_superuser 绕过 RBAC）。
--          删除仅影响管理端导航展示，不影响后端 API / 数据。若日后补建对应 .vue 视图，
--          重跑 codegen 即可恢复菜单（可逆）。
-- 幂等: 全程按 path/name/perms 匹配，可在 local(15432) 与生产(5432) 重复执行。
-- =====================================================

BEGIN;

-- ───────────────────────── ① 删整目录死菜单/测试垃圾 ─────────────────────────
WITH RECURSIVE roots AS (
    SELECT id FROM sys_menu
    WHERE parent_id IS NULL AND type = 0
      AND path IN ('/hermes', '/lead_automation_smoke', '/hasn_community', '/deck', '/codegen_test')
),
subtree AS (
    SELECT id FROM sys_menu WHERE id IN (SELECT id FROM roots)
    UNION ALL
    SELECT m.id FROM sys_menu m JOIN subtree s ON m.parent_id = s.id
)
DELETE FROM sys_menu WHERE id IN (SELECT id FROM subtree);

-- ───────────────────────── ② 删 HASN 目录下的死页面 + 其按钮 ─────────────────────────
-- （前端 src/views<path>/index.vue 均不存在）
WITH RECURSIVE dead_pages AS (
    SELECT id FROM sys_menu
    WHERE type = 1 AND path IN (
        '/hasn/hasn_channel_bindings',
        '/hasn/hasn_pending_intents',
        '/hasn/hasn_suppressed_messages',
        '/hasn/hasn_sync_events',
        '/hasn/hasn_sync_inbox_events',
        '/hasn/hasn_tenant_sandboxes',
        '/hasn/hasn_posts',
        '/hasn/hasn_articles',
        '/hasn/hasn_comments',
        '/hasn/hasn_follows',
        '/hasn/hasn_likes',
        '/hasn/hasn_collections',
        '/hasn/hasn_collection_items',
        '/hasn/hasn_sessions',
        '/hasn/hasn_session_events',
        '/hasn/hasn_session_artifacts',
        '/hasn/hasn_contact_requests',
        '/hasn/hasn_workbench_briefing',
        '/hasn/hasn_workbench_briefing_feedback',
        '/hasn/hasn_agent_channel_mirrors',
        '/hasn/hasn_app_catalog',
        '/hasn/hasn_app_entitlement'
    )
),
subtree AS (
    SELECT id FROM dead_pages
    UNION ALL
    SELECT m.id FROM sys_menu m JOIN subtree s ON m.parent_id = s.id
)
DELETE FROM sys_menu WHERE id IN (SELECT id FROM subtree);

-- ───────────────────────── ③ 删重复按钮（下划线权限格式，后端未用） ─────────────────────────
DELETE FROM sys_menu
WHERE type = 2 AND perms IN (
    'hasn:agent_capabilities:add', 'hasn:agent_capabilities:edit',
    'hasn:agent_capabilities:del', 'hasn:agent_capabilities:get',
    'hasn:audit_log:add', 'hasn:audit_log:edit', 'hasn:audit_log:del', 'hasn:audit_log:get',
    'hasn:group_members:add', 'hasn:group_members:edit', 'hasn:group_members:del', 'hasn:group_members:get',
    'hasn:trade_sessions:add', 'hasn:trade_sessions:edit', 'hasn:trade_sessions:del', 'hasn:trade_sessions:get',
    'hasn:unread_counts:add', 'hasn:unread_counts:edit', 'hasn:unread_counts:del', 'hasn:unread_counts:get'
);

-- ───────────────────────── ④ 删孤儿按钮（父页已不存在的悬空按钮） ─────────────────────────
DELETE FROM sys_menu m
WHERE m.type = 2
  AND m.parent_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM sys_menu p WHERE p.id = m.parent_id);

-- ───────────────────────── ⑤ 清理 sys_role_menu 悬空引用 ─────────────────────────
DELETE FROM sys_role_menu rm
WHERE NOT EXISTS (SELECT 1 FROM sys_menu m WHERE m.id = rm.menu_id);

-- ───────────────────────── ⑥ 重名标题优化（按 name 精确匹配，幂等） ─────────────────────────
-- 应用平台（App Platform）下 8 个 "App管理"
UPDATE sys_menu SET title = '应用清单'     WHERE name = 'AppManifests';
UPDATE sys_menu SET title = '应用版本管理' WHERE name = 'AppVersions';
UPDATE sys_menu SET title = '应用安装'     WHERE name = 'AppInstallations';
UPDATE sys_menu SET title = '应用工具'     WHERE name = 'AppTools';
UPDATE sys_menu SET title = '应用资源'     WHERE name = 'AppResources';
UPDATE sys_menu SET title = '应用事件'     WHERE name = 'AppEvents';
UPDATE sys_menu SET title = '应用评价'     WHERE name = 'AppReviews';
UPDATE sys_menu SET title = '应用权益'     WHERE name = 'AppEntitlements';

-- HASN 社交网络下 4 个 "HASN管理"
UPDATE sys_menu SET title = 'HASN客户端'   WHERE name = 'HasnClients';
UPDATE sys_menu SET title = 'HASN节点'     WHERE name = 'HasnNodes';
UPDATE sys_menu SET title = 'Owner API 密钥' WHERE name = 'HasnOwnerApiKeys';
UPDATE sys_menu SET title = 'HASN节点绑定' WHERE name = 'HasnNodeBindings';

-- 线索自动化下 4 个 "Lead管理" + 2 个 "AI管理"
UPDATE sys_menu SET title = '线索联系人来源' WHERE name = 'LeadContactSource';
UPDATE sys_menu SET title = '线索导出批次'   WHERE name = 'LeadExportBatch';
UPDATE sys_menu SET title = '线索导出明细'   WHERE name = 'LeadExportItem';
UPDATE sys_menu SET title = '线索审计日志'   WHERE name = 'LeadAuditLog';
UPDATE sys_menu SET title = '线索源配置'     WHERE name = 'LeadSourceConfig';
UPDATE sys_menu SET title = '线索采集任务'   WHERE name = 'LeadCollectionJob';

-- 3 个 "用户管理"（系统用户保持「用户管理」，另两处语义化）
UPDATE sys_menu SET title = '唤星用户' WHERE name = 'HuanxingUser';
UPDATE sys_menu SET title = 'HASN用户' WHERE name = 'HasnHumans';

-- 2 个 "应用版本"（市场侧加前缀区分；版本管理目录下保持「应用版本」）
UPDATE sys_menu SET title = '市场应用版本' WHERE name = 'MarketplaceAppVersion';

-- ───────────────────────── ⑥' 英文目录名 → 中文名 + 语义图标 ─────────────────────────
UPDATE sys_menu SET title = '线索自动化', icon = 'ant-design:funnel-plot-outlined'
    WHERE name = 'Lead_automation' AND parent_id IS NULL AND type = 0;
UPDATE sys_menu SET title = '应用平台',   icon = 'ant-design:appstore-add-outlined'
    WHERE name = 'App_platform'    AND parent_id IS NULL AND type = 0;
UPDATE sys_menu SET title = '外部桥接',   icon = 'ant-design:api-outlined'
    WHERE name = 'Bridge'          AND parent_id IS NULL AND type = 0;
UPDATE sys_menu SET title = '集成管理',   icon = 'ant-design:deployment-unit-outlined'
    WHERE name = 'Integration'     AND parent_id IS NULL AND type = 0;

-- 「关于」目录的外链图标 → 本地语义图标（去掉外部网络依赖）
UPDATE sys_menu SET icon = 'ant-design:info-circle-outlined'
    WHERE name = 'Project' AND parent_id IS NULL AND type = 0
      AND icon LIKE 'http%';

COMMIT;
