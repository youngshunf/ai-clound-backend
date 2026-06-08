-- =====================================================
-- 分身命名体系重构：hasn_agents 补「领域专家头衔」profession 列
-- 三者职责分离：
--   profession  领域专家头衔（金融专家），来自模板 name，与人名并列展示
--   display_name 像人一样的名字（明远），全局唯一（应用层校验）
--   agent_name   技术 slug（finance/finance-2），派生 star_id + (owner,agent_name) 幂等
-- 创建分身时由 webui/daemon 据所选模板的 name 透传，云端落库。存量行 profession 为空，
-- UI 端按 role/template_id 兜底显示，不回填(无权威来源)。
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "profession" varchar(60);

COMMENT ON COLUMN "public"."hasn_agents"."profession" IS '领域专家头衔（如「金融专家」，来自模板 name；与人名 display_name 并列展示）';
