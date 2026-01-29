/*
 Navicat Premium Dump SQL

 Source Server         : localhost_5432
 Source Server Type    : PostgreSQL
 Source Server Version : 160009 (160009)
 Source Host           : localhost:5432
 Source Catalog        : ai_creator
 Source Schema         : public

 Target Server Type    : PostgreSQL
 Target Server Version : 160009 (160009)
 File Encoding         : 65001

 Date: 28/01/2026 20:57:36
*/


-- ----------------------------
-- Table structure for llm_model_config
-- ----------------------------
DROP TABLE IF EXISTS "public"."llm_model_config";
CREATE SEQUENCE IF NOT EXISTS "public"."llm_model_config_id_seq";
CREATE TABLE "public"."llm_model_config" (
  "id" int8 NOT NULL DEFAULT nextval('llm_model_config_id_seq'::regclass),
  "provider_id" int8 NOT NULL,
  "model_name" varchar(128) COLLATE "pg_catalog"."default" NOT NULL,
  "model_type" varchar(32) COLLATE "pg_catalog"."default" NOT NULL,
  "display_name" varchar(128) COLLATE "pg_catalog"."default",
  "max_tokens" int4 NOT NULL,
  "max_context_length" int4 NOT NULL,
  "supports_streaming" bool NOT NULL,
  "supports_tools" bool NOT NULL,
  "supports_vision" bool NOT NULL,
  "input_cost_per_1k" numeric(10,6) NOT NULL,
  "output_cost_per_1k" numeric(10,6) NOT NULL,
  "rpm_limit" int4,
  "tpm_limit" int4,
  "priority" int4 NOT NULL,
  "enabled" bool NOT NULL,
  "created_time" timestamptz(6) NOT NULL,
  "updated_time" timestamptz(6)
)
;
ALTER SEQUENCE "public"."llm_model_config_id_seq" OWNED BY "public"."llm_model_config"."id";
ALTER TABLE "public"."llm_model_config" OWNER TO "n8n";
COMMENT ON COLUMN "public"."llm_model_config"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."llm_model_config"."provider_id" IS '供应商 ID';
COMMENT ON COLUMN "public"."llm_model_config"."model_name" IS '模型名称';
COMMENT ON COLUMN "public"."llm_model_config"."model_type" IS '模型类型';
COMMENT ON COLUMN "public"."llm_model_config"."display_name" IS '显示名称';
COMMENT ON COLUMN "public"."llm_model_config"."max_tokens" IS '最大输出 tokens';
COMMENT ON COLUMN "public"."llm_model_config"."max_context_length" IS '最大上下文长度';
COMMENT ON COLUMN "public"."llm_model_config"."supports_streaming" IS '支持流式';
COMMENT ON COLUMN "public"."llm_model_config"."supports_tools" IS '支持工具调用';
COMMENT ON COLUMN "public"."llm_model_config"."supports_vision" IS '支持视觉';
COMMENT ON COLUMN "public"."llm_model_config"."input_cost_per_1k" IS '输入成本/1K tokens (USD)';
COMMENT ON COLUMN "public"."llm_model_config"."output_cost_per_1k" IS '输出成本/1K tokens (USD)';
COMMENT ON COLUMN "public"."llm_model_config"."rpm_limit" IS '模型 RPM 限制';
COMMENT ON COLUMN "public"."llm_model_config"."tpm_limit" IS '模型 TPM 限制';
COMMENT ON COLUMN "public"."llm_model_config"."priority" IS '优先级(越大越优先)';
COMMENT ON COLUMN "public"."llm_model_config"."enabled" IS '是否启用';
COMMENT ON COLUMN "public"."llm_model_config"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."llm_model_config"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."llm_model_config" IS '模型配置表';

-- ----------------------------
-- Records of llm_model_config
-- ----------------------------
BEGIN;
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (27, 3, 'qwen-flash', 'TEXT', 'qwen-flash', 4096, 8192, 't', 't', 'f', 0.000000, 0.000000, NULL, NULL, 201, 't', '2025-12-29 14:19:45.501663+00', '2025-12-29 14:19:57.644335+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (28, 3, 'deepseek-v3.2', 'REASONING', 'deepseek-v3.2', 20000, 20000, 't', 'f', 'f', 0.000000, 0.000000, NULL, NULL, 202, 't', '2025-12-29 14:21:01.538563+00', '2025-12-29 14:21:10.415301+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (29, 3, 'qwen-vl-max-2025-08-13', 'VISION', 'qwen-vl-max-2025-08-13', 4096, 8192, 't', 'f', 't', 0.000000, 0.000000, NULL, NULL, 100, 't', '2025-12-29 14:21:57.927826+00', NULL);
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (26, 10, 'claude-sonnet-4-5-20250929', 'TEXT', 'claude-sonnet-4-5', 4096, 8192, 't', 't', 't', 0.000000, 0.000000, NULL, NULL, 500, 't', '2025-12-29 11:45:16.782749+00', '2026-01-13 08:48:41.502654+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (23, 10, 'claude-opus-4-5-20251101', 'TEXT', 'claude-opus-4-5', 25000, 25000, 't', 'f', 'f', 0.000000, 0.000000, NULL, NULL, 500, 't', '2025-12-29 04:49:01.845735+00', '2026-01-13 08:48:53.923555+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (31, 11, 'claude-sonnet-4-5-thinking', 'TEXT', 'claude-sonnet-4-5-thinking', 12800, 25000, 't', 't', 't', 0.000000, 0.000000, NULL, NULL, 0, 'f', '2026-01-05 12:45:47.571353+00', '2026-01-21 12:24:01.749729+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (30, 11, 'claude-opus-4-5-thinking', 'REASONING', 'claude-opus-4-5-thinking', 12800, 25000, 't', 't', 't', 0.000000, 0.000000, NULL, NULL, 300, 'f', '2026-01-05 12:42:09.519377+00', '2026-01-21 12:24:02.66991+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (25, 10, 'claude-haiku-4-5-20251001', 'TEXT', 'claude-haiku-4-5', 25000, 25000, 't', 'f', 'f', 0.000000, 0.000000, NULL, NULL, 190, 'f', '2025-12-29 11:41:47.21916+00', '2026-01-21 12:24:05.140185+00');
INSERT INTO "public"."llm_model_config" ("id", "provider_id", "model_name", "model_type", "display_name", "max_tokens", "max_context_length", "supports_streaming", "supports_tools", "supports_vision", "input_cost_per_1k", "output_cost_per_1k", "rpm_limit", "tpm_limit", "priority", "enabled", "created_time", "updated_time") VALUES (24, 10, 'claude-sonnet-4-5-tinking', 'REASONING', 'claude-sonnet-4-5-20250929', 25000, 25000, 't', 't', 't', 0.000000, 0.000000, NULL, NULL, 501, 't', '2025-12-29 11:05:19.523587+00', '2026-01-21 12:24:32.319669+00');
COMMIT;

-- ----------------------------
-- Indexes structure for table llm_model_config
-- ----------------------------
CREATE INDEX "ix_llm_model_config_enabled" ON "public"."llm_model_config" USING btree (
  "enabled" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE UNIQUE INDEX "ix_llm_model_config_id" ON "public"."llm_model_config" USING btree (
  "id" "pg_catalog"."int8_ops" ASC NULLS LAST
);
CREATE INDEX "ix_llm_model_config_model_name" ON "public"."llm_model_config" USING btree (
  "model_name" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_llm_model_config_model_type" ON "public"."llm_model_config" USING btree (
  "model_type" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_llm_model_config_provider_id" ON "public"."llm_model_config" USING btree (
  "provider_id" "pg_catalog"."int8_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table llm_model_config
-- ----------------------------
ALTER TABLE "public"."llm_model_config" DROP CONSTRAINT IF EXISTS "llm_model_config_pkey";
ALTER TABLE "public"."llm_model_config" ADD CONSTRAINT "llm_model_config_pkey" PRIMARY KEY ("id");
