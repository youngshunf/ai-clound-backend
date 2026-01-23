-- MySQL/PostgreSQL Compatible Menu SQL for Topic Module

-- 1. Topic Management (Parent Menu)
INSERT INTO sys_menu (title, name, path, component, icon, sort, type, status, display, cache, parent_id, created_time, updated_time)
SELECT '选题管理', 'Topic', '/topic', 'BasicLayout', 'material-symbols:topic-outline', 2, 0, 1, 1, 1, NULL, NOW(), NULL
WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE name = 'Topic');

-- 3. Industry Management
INSERT INTO sys_menu (title, name, path, component, icon, sort, type, status, display, cache, parent_id, perms, created_time, updated_time)
SELECT '行业管理', 'IndustryManage', '/topic/industry', '/topic/industry/index', 'carbon:industry', 1, 1, 1, 1, 1, (SELECT id FROM sys_menu WHERE name = 'Topic' LIMIT 1), 'topic:industry:list', NOW(), NULL
WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE name = 'IndustryManage');

-- 4. Topic Recommendation
INSERT INTO sys_menu (title, name, path, component, icon, sort, type, status, display, cache, parent_id, perms, created_time, updated_time)
SELECT '选题推荐', 'TopicRecommendation', '/topic/manage', '/topic/manage/index', 'material-symbols:recommend-outline', 2, 1, 1, 1, 1, (SELECT id FROM sys_menu WHERE name = 'Topic' LIMIT 1), 'topic:recommendation:list', NOW(), NULL
WHERE NOT EXISTS (SELECT 1 FROM sys_menu WHERE name = 'TopicRecommendation');
