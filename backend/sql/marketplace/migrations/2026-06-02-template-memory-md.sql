-- =====================================================
-- Agent Profile：marketplace_template 补 MEMORY.md 内容列
-- 配套：唤星 Agent 创建链路修复（模板变量替换 / 缺 MEMORY.md / USER·MEMORY 格式 / 重复创建覆盖）
-- 由 github_app_sync_service 同步 hasn-hub 时抽取（per-template 优先、回退 templates/MEMORY.md）入库；
-- 创建 Agent 时云端据此种子进 hasn_agents.memory_md（Agent 长期/自我演化记忆，§ 记录格式）。
-- =====================================================

ALTER TABLE "public"."marketplace_template"
  ADD COLUMN IF NOT EXISTS "memory_md" text;

COMMENT ON COLUMN "public"."marketplace_template"."memory_md" IS '模板 MEMORY.md 内容（Agent 长期/自我演化记忆种子，§ 记录格式，创建时种子进 hasn_agents.memory_md）';
