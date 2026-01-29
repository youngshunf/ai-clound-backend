-- =====================================================
-- 模型别名映射菜单 SQL
-- @author Ysf
-- @date 2025-01-28
-- =====================================================

-- LLM 模型别名映射菜单（放在模型配置后面）
-- 注意：ID 需要根据实际数据库中已有的 ID 调整，避免冲突
-- 当前使用 123-126 作为菜单 ID

INSERT INTO sys_menu (id, title, name, path, sort, icon, type, component, perms, status, display, cache, link, remark, parent_id, created_time, updated_time)
VALUES
(123, '模型映射', 'LlmModelAlias', '/llm/model-alias', 2.5, 'carbon:connect', 1, '/llm/model-alias/index', NULL, 1, 1, 1, '', 'Anthropic 模型别名映射管理，用于将 Claude 模型名映射到实际可用模型', 100, NOW(), NULL),
(124, '新增', 'AddLlmModelAlias', NULL, 0, NULL, 2, NULL, 'llm:model-alias:add', 1, 0, 1, '', NULL, 123, NOW(), NULL),
(125, '修改', 'EditLlmModelAlias', NULL, 0, NULL, 2, NULL, 'llm:model-alias:edit', 1, 0, 1, '', NULL, 123, NOW(), NULL),
(126, '删除', 'DeleteLlmModelAlias', NULL, 0, NULL, 2, NULL, 'llm:model-alias:del', 1, 0, 1, '', NULL, 123, NOW(), NULL);

-- =====================================================
-- 为管理员角色分配模型别名菜单权限（可选）
-- 假设管理员角色 ID 为 1
-- =====================================================
-- INSERT INTO sys_role_menu (role_id, menu_id)
-- SELECT 1, id FROM sys_menu WHERE id BETWEEN 123 AND 126;
