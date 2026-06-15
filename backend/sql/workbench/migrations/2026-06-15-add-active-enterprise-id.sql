-- 应用平台 v3 P3：身份上下文瘦指针 active_enterprise_id（替代 hasn_user_active_workspace）
-- 事实源：docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §4.2(1)
--
-- 「彻底废 workspace 重实体」保住身份上下文：删的是 workspace 重实体（绑 app / 绑实例 /
-- 挂成员），不是「我现在以谁的身份操作」。active_enterprise_id 瘦指针（null=个人）回答
-- 新产物归属 + 计费 holder + 列表过滤，落在 owner 的工作台偏好行（owner-scoped 每人一行）。
--
-- 幂等：可重复执行。

-- ① 加列（pref 表在 hasn_workbench schema）
ALTER TABLE hasn_workbench.hasn_owner_workbench_pref
  ADD COLUMN IF NOT EXISTS active_enterprise_id BIGINT;

COMMENT ON COLUMN hasn_workbench.hasn_owner_workbench_pref.active_enterprise_id
  IS '当前企业上下文 ID（null=个人；非 null=以该企业身份操作），替代已退役的 hasn_user_active_workspace';

-- ② 数据回填：hasn_user_active_workspace kind=enterprise 行 → active_enterprise_id
--    （按 user_id→owner_hasn_id 经 hasn_humans 映射到 pref.owner_hasn_id；
--     kind=personal / 无行 → 保持 null）
UPDATE hasn_workbench.hasn_owner_workbench_pref pref
SET active_enterprise_id = aw.enterprise_id,
    updated_time = NOW()
FROM public.hasn_user_active_workspace aw
JOIN public.hasn_humans h ON h.user_id = aw.user_id
WHERE pref.owner_hasn_id = h.hasn_id
  AND aw.kind = 'enterprise'
  AND aw.enterprise_id IS NOT NULL
  AND pref.active_enterprise_id IS NULL;
