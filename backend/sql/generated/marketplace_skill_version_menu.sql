-- =====================================================
-- 技能版本管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-01-28 20:42:39.561102
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /marketplace)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/marketplace' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Marketplace', 'Marketplace', '/marketplace', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'marketplace模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /marketplace/marketplace_skill_version)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/marketplace/marketplace_skill_version' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('技能版本管理', 'MarketplaceSkillVersion', '/marketplace/marketplace_skill_version', 1, 'lucide:list', 1, '/marketplace/marketplace_skill_version/index', NULL, 1, 1, 1, '', '技能版本表', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '技能版本管理',
            name = 'MarketplaceSkillVersion',
            component = '/marketplace/marketplace_skill_version/index',
            remark = '技能版本表',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:skill:version:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddMarketplaceSkillVersion', NULL, 1, NULL, 2, NULL, 'marketplace:skill:version:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:skill:version:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditMarketplaceSkillVersion', NULL, 2, NULL, 2, NULL, 'marketplace:skill:version:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:skill:version:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteMarketplaceSkillVersion', NULL, 3, NULL, 2, NULL, 'marketplace:skill:version:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'marketplace:skill:version:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewMarketplaceSkillVersion', NULL, 4, NULL, 2, NULL, 'marketplace:skill:version:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
