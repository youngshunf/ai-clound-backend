-- =====================================================
-- Agent Profile 云端权威化 (P1)：marketplace_template 补 SOUL/AGENTS/USER 内容列
-- ADR: decisions/architecture/2026-05-30-agent-profile-cloud-authoritative.md §4
-- 由 github_app_sync_service 同步 hasn-hub 模板时抽取 *.md 入库；
-- 创建 Agent 时云端据此物化进 hasn_agents.{soul_md,agents_md,user_md}。
-- =====================================================

ALTER TABLE "public"."marketplace_template"
  ADD COLUMN IF NOT EXISTS "soul_md"   text,
  ADD COLUMN IF NOT EXISTS "agents_md" text,
  ADD COLUMN IF NOT EXISTS "user_md"   text;

COMMENT ON COLUMN "public"."marketplace_template"."soul_md"   IS '模板 SOUL.md 内容（Agent 身份档案，创建时物化进 hasn_agents.soul_md）';
COMMENT ON COLUMN "public"."marketplace_template"."agents_md" IS '模板 AGENTS.md 内容（Agent 行为指南，创建时物化进 hasn_agents.agents_md）';
COMMENT ON COLUMN "public"."marketplace_template"."user_md"   IS '模板 USER.md 内容（主人信息种子，创建时物化进 hasn_agents.user_md）';
