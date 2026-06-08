-- =====================================================
-- Agent 模板：marketplace_template 补「候选人名池」name_pool 列
-- 配套：分身命名体系重构——name 改为领域专家头衔(金融专家)、人名与专家头衔分离。
-- 由 github_app_sync_service 同步 huanxing-hub 时从 template.yaml 的 name_pool 抽取
-- (逗号拼接入库)；创建分身时 webui 据此供用户选择人名(display_name)，云端落库时
-- 全局唯一校验、重名给建议。display_name 取池首位作兜底。
-- =====================================================

ALTER TABLE "public"."marketplace_template"
  ADD COLUMN IF NOT EXISTS "name_pool" varchar(500);

COMMENT ON COLUMN "public"."marketplace_template"."name_pool" IS '候选人名池（逗号分隔，仅 Agent 模板；创建分身时供用户选择/兜底，display_name 取池首位）';
