-- =====================================================
-- 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）管理 菜单初始化 SQL (PostgreSQL)
-- 自动生成于: 2026-06-23 10:50:05.037282
-- 支持幂等操作：已存在则更新，不存在则新增
-- =====================================================

DO $$
DECLARE
    v_parent_id INTEGER;
    v_menu_id INTEGER;
BEGIN
    -- 查找或创建父级目录菜单 (path = /hasn_studio)
    SELECT id INTO v_parent_id FROM sys_menu 
    WHERE path = '/hasn_studio' AND type = 0
    ORDER BY id LIMIT 1;
    
    IF v_parent_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('Hasn Studio', 'Hasn_studio', '/hasn_studio', 1, 'lucide:folder', 0, 'BasicLayout', NULL, 1, 1, 1, '', 'hasn_studio模块', NULL, NOW(), NULL)
        RETURNING id INTO v_parent_id;
    END IF;

    -- 查找或创建主菜单 (path = /hasn_studio/studio_asset)
    SELECT id INTO v_menu_id FROM sys_menu 
    WHERE path = '/hasn_studio/studio_asset' AND type = 1
    ORDER BY id LIMIT 1;
    
    IF v_menu_id IS NULL THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）管理', 'StudioAsset', '/hasn_studio/studio_asset', 1, 'lucide:list', 1, '/hasn_studio/studio_asset/index', NULL, 1, 1, 1, '', '视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）', v_parent_id, NOW(), NULL)
        RETURNING id INTO v_menu_id;
    ELSE
        UPDATE sys_menu SET
            title = '视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）管理',
            name = 'StudioAsset',
            component = '/hasn_studio/studio_asset/index',
            remark = '视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）',
            parent_id = v_parent_id,
            updated_time = NOW()
        WHERE id = v_menu_id;
    END IF;

    -- 新增按钮（按 perms 判断）
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'studio:asset:add' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('新增', 'AddStudioAsset', NULL, 1, NULL, 2, NULL, 'studio:asset:add', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 编辑按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'studio:asset:edit' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('编辑', 'EditStudioAsset', NULL, 2, NULL, 2, NULL, 'studio:asset:edit', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 删除按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'studio:asset:del' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('删除', 'DeleteStudioAsset', NULL, 3, NULL, 2, NULL, 'studio:asset:del', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;

    -- 查看按钮
    IF NOT EXISTS (SELECT 1 FROM sys_menu WHERE perms = 'studio:asset:get' AND parent_id = v_menu_id) THEN
        INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
        VALUES ('查看', 'ViewStudioAsset', NULL, 4, NULL, 2, NULL, 'studio:asset:get', 1, 0, 1, '', NULL, v_menu_id, NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 菜单生成完成
-- =====================================================
