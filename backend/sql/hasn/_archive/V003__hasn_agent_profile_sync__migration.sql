-- =====================================================
-- HASN Agent Profile Sync / Cloud-first Create Migration
-- - Cloud hasn_agents is the Agent Profile authority.
-- - Does not modify hasn_humans.
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "template_id" varchar(80),
  ADD COLUMN IF NOT EXISTS "skills" jsonb,
  ADD COLUMN IF NOT EXISTS "soul_md" text,
  ADD COLUMN IF NOT EXISTS "user_md" text,
  ADD COLUMN IF NOT EXISTS "profile_source" varchar(20) NOT NULL DEFAULT 'cloud',
  ADD COLUMN IF NOT EXISTS "profile_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "deleted_at" timestamptz(6);

CREATE INDEX IF NOT EXISTS "idx_hasn_agents_owner_profile_revision"
  ON "public"."hasn_agents" ("owner_id", "profile_revision");
CREATE INDEX IF NOT EXISTS "idx_hasn_agents_template"
  ON "public"."hasn_agents" ("template_id") WHERE "template_id" IS NOT NULL;

COMMENT ON COLUMN "public"."hasn_agents"."template_id" IS 'Agent 模板 ID（来自 Agent 市场，可空表示自定义）';
COMMENT ON COLUMN "public"."hasn_agents"."skills" IS 'Agent 技能配置 JSON（云端 Profile 配置源）';
COMMENT ON COLUMN "public"."hasn_agents"."soul_md" IS 'Agent SOUL.md 内容（云端 Profile 配置源）';
COMMENT ON COLUMN "public"."hasn_agents"."user_md" IS 'Agent USER.md 内容（云端 Profile 配置源）';
COMMENT ON COLUMN "public"."hasn_agents"."profile_source" IS 'Profile 来源 (cloud:云端事实源:green/imported:导入:blue)';
COMMENT ON COLUMN "public"."hasn_agents"."profile_revision" IS 'Agent Profile 修订号';
COMMENT ON COLUMN "public"."hasn_agents"."deleted_at" IS '软删除时间';

CREATE TABLE IF NOT EXISTS "public"."hasn_agent_templates" (
  "id"                   bigserial PRIMARY KEY,
  "template_id"          varchar(80) NOT NULL,
  "name"                 varchar(100) NOT NULL,
  "description"          text,
  "avatar"               varchar(500),
  "category"             varchar(50),
  "tags"                 jsonb,
  "default_skills"       jsonb,
  "default_soul_md"      text,
  "default_user_md"      text,
  "default_description"  text,
  "default_runtime_type" varchar(30) NOT NULL DEFAULT 'hermes',
  "status"               varchar(20) NOT NULL DEFAULT 'active',
  "sort_order"           int NOT NULL DEFAULT 0,
  "created_time"         timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"         timestamptz(6),
  CONSTRAINT "uq_hasn_agent_templates_template_id" UNIQUE ("template_id")
);

CREATE INDEX IF NOT EXISTS "idx_hasn_agent_templates_status_sort"
  ON "public"."hasn_agent_templates" ("status", "sort_order", "id");

COMMENT ON TABLE "public"."hasn_agent_templates" IS 'HASN Agent 市场模板表';
COMMENT ON COLUMN "public"."hasn_agent_templates"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_agent_templates"."template_id" IS '模板唯一 ID';
COMMENT ON COLUMN "public"."hasn_agent_templates"."name" IS '模板名称';
COMMENT ON COLUMN "public"."hasn_agent_templates"."description" IS '模板说明';
COMMENT ON COLUMN "public"."hasn_agent_templates"."avatar" IS '模板默认头像';
COMMENT ON COLUMN "public"."hasn_agent_templates"."category" IS '模板分类';
COMMENT ON COLUMN "public"."hasn_agent_templates"."tags" IS '模板标签 JSON';
COMMENT ON COLUMN "public"."hasn_agent_templates"."default_skills" IS '默认技能配置 JSON';
COMMENT ON COLUMN "public"."hasn_agent_templates"."default_soul_md" IS '默认 SOUL.md';
COMMENT ON COLUMN "public"."hasn_agent_templates"."default_user_md" IS '默认 USER.md';
COMMENT ON COLUMN "public"."hasn_agent_templates"."default_description" IS '默认 Agent 简介';
COMMENT ON COLUMN "public"."hasn_agent_templates"."default_runtime_type" IS '默认 Runtime 类型 (hermes:Hermes:green/claude_code:Claude Code:purple/codex:Codex:blue/none:无:gray)';
COMMENT ON COLUMN "public"."hasn_agent_templates"."status" IS '状态 (active:活跃:green/disabled:停用:orange/deleted:删除:red)';
COMMENT ON COLUMN "public"."hasn_agent_templates"."sort_order" IS '排序值';
COMMENT ON COLUMN "public"."hasn_agent_templates"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_agent_templates"."updated_time" IS '更新时间';
