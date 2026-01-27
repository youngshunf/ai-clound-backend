-- =====================================================
-- 用户订阅表 - 管理用户的订阅等级和积分余额 菜单初始化 SQL (MySQL)
-- 自动生成于: 2026-01-27 19:15:49.100800+08:00
-- =====================================================

-- 父级菜单
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('用户订阅表 - 管理用户的订阅等级和积分余额', 'UserSubscription', '/llm', 1, 'lucide:list', 1, '#/views/llm/index.vue', NULL, 1, 1, 1, '', '用户订阅表 - 管理用户的订阅等级和积分余额管理', NULL, NOW(), NULL);

-- 获取刚插入的父菜单 ID
SET @parent_menu_id = LAST_INSERT_ID();

-- 新增按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('新增', 'AddUserSubscription', NULL, 1, NULL, 2, NULL, 'user:subscription:add', 1, 0, 1, '', NULL, @parent_menu_id, NOW(), NULL);

-- 编辑按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('编辑', 'EditUserSubscription', NULL, 2, NULL, 2, NULL, 'user:subscription:edit', 1, 0, 1, '', NULL, @parent_menu_id, NOW(), NULL);

-- 删除按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('删除', 'DeleteUserSubscription', NULL, 3, NULL, 2, NULL, 'user:subscription:del', 1, 0, 1, '', NULL, @parent_menu_id, NOW(), NULL);

-- 查看按钮
INSERT INTO sys_menu (title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
('查看', 'ViewUserSubscription', NULL, 4, NULL, 2, NULL, 'user:subscription:get', 1, 0, 1, '', NULL, @parent_menu_id, NOW(), NULL);

-- =====================================================
-- 菜单生成完成
-- =====================================================
