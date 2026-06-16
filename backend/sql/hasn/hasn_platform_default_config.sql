-- =====================================================
-- 平台默认配置（云端权威，单行下发）
-- 一行权威（config_key='global'）：运营 Admin 改一处，全平台桌面端 daemon + runtime 自动应用
--   - node.media：节点级媒体模型默认（image/tts/stt failover 顺序）
--   - agent_runtime.models：平台默认 agent 运行时四槽模型（main/fast/vision/delegation；分身未显式设该槽时回落）
-- 变更传播：revision = sha256(canonical_json(config_json))[:16]，daemon 比对 revision 变化即重拉
-- 形态属"配置/元数据单行"（非实体 CRUD），故沿用 namespace_revision/hasn_app_catalog 同类手写约定，
-- 不走 4-scope fba 代码生成；Admin GET/PUT + 节点 GET 由 service/API 自定义。
-- 设计事实源：docs/hasn-node设计文档/运行时配置下发/01-平台默认配置下发机制.md
-- 备注：无 seed 行——service 在无行时返回出厂默认 + 其确定性 revision（零 fake）；首次 Admin 保存即建行。
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_platform_default_config" (
  "id"           bigserial      PRIMARY KEY,
  "config_key"   varchar(32)    NOT NULL DEFAULT 'global',
  "config_json"  jsonb          NOT NULL DEFAULT '{}'::jsonb,
  "revision"     varchar(16)    NOT NULL DEFAULT '',
  "updated_by"   varchar(64),
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6),
  CONSTRAINT "uq_platform_default_config_key" UNIQUE ("config_key")
);

COMMENT ON TABLE "public"."hasn_platform_default_config" IS '平台默认配置（云端权威，单行下发）';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."config_key" IS '配置键（单行权威，恒 global）';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."config_json" IS '平台默认配置 JSON（node.media + agent_runtime.models）';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."revision" IS '配置内容指纹 sha256(canonical_json)[:16]，daemon 比对重拉';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."updated_by" IS '最后修改管理员（用户名/ID）';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_platform_default_config"."updated_time" IS '更新时间';
