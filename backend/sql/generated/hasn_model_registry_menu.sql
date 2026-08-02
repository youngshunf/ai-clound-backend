-- =====================================================
-- 模型注册表 菜单初始化 SQL (PostgreSQL)
-- 幂等：已存在则更新，不存在则新增
--
-- 与 codegen 原始产物的差异（人工收敛，勿再让 codegen 覆盖）：
--   1. 标题取「模型注册表」——codegen 用表注释截断到第一个括号，会生成「模型注册表（new-api管理」；
--   2. 路由收敛到 /hasn/model_registry（前端页面同名），不用带表名前缀的 /hasn/hasn_model_registry；
--   3. **只保留 edit 一个权限点**：注册表的行只能来自 new-api 同步，没有新增/删除端点
--      （手工新增等于放开手输模型名，正是要消灭的事故根因；删行会连人工标注一起丢）。
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn)
    SELECT id INTO v_parent_id FROM sys_menu
    WHERE path = '/hasn' AND type = 0
    ORDER BY id LIMIT 1;

    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn', 'Hasn', '/hasn', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn/model_registry)
    SELECT id INTO v_menu_id FROM sys_menu
    WHERE path = '/hasn/model_registry' AND type = 1
    ORDER BY id LIMIT 1;

    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('模型注册表', 'HasnModelRegistry', '/hasn/model_registry', 2, 'lucide:boxes', 1, '/hasn/model_registry/index', NULL, 1, 1, 1, '', '模型清单从 new-api 同步 + 能力语义人工标注', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '模型注册表',
            name = 'HasnModelRegistry',
            component = '/hasn/model_registry/index',
            remark = '模型清单从 new-api 同步 + 能力语义人工标注',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 唯一权限点：标注 + 立即同步（两者都是写动作，归同一角色）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'hasn:model:registry:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('标注与同步', 'EditHasnModelRegistry', NULL, 1, NULL, 2, NULL, 'hasn:model:registry:edit', 1, 0, 1, '', '标注能力语义 + 从网关立即同步', v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
