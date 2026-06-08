-- =====================================================
-- 演示文稿系统（模块 17）云端权威：style_profile 可复用样式（style-skill）
-- 与 deck 解耦的可复用视觉预设。builtin 随客户端内置（代码权威，不入云）；custom 由 owner 创建 → 云端权威 + 同步
-- 主键 bigint 自增（对齐 fba id_key）；slug 为人读标识（同 owner 下唯一）；端云经 server_id 映射，无 uid。
-- 生成：uv run fba codegen generate --sql-file backend/sql/deck/style_profile.sql --app deck --schema deck --execute
-- 设计事实源：docs/hasn-node设计文档/17-演示文稿系统/01-数据模型.md §7/§9
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "deck";

CREATE TABLE "deck"."style_profile" (
  "id"              bigserial      PRIMARY KEY,
  "slug"            varchar(64)    NOT NULL,
  "label"           varchar(128)   NOT NULL,
  "description"     text,
  "source"          varchar(16)    NOT NULL DEFAULT 'custom',
  "design_contract" jsonb,
  "style_prompt"    text,
  "owner_id"        varchar(40)    NOT NULL,
  "rev"             bigint         NOT NULL DEFAULT 1,
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6),
  "deleted_time"    timestamptz(6)
);

-- 同一 owner 下 slug 在未删行内唯一
CREATE UNIQUE INDEX "uq_style_profile_owner_slug" ON "deck"."style_profile" ("owner_id", "slug") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "deck"."style_profile" IS '演示文稿可复用样式 StyleProfile（云端权威，仅 custom）';
COMMENT ON COLUMN "deck"."style_profile"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "deck"."style_profile"."slug" IS '样式 slug（如 minimal-white；同 owner 下唯一；人读标识）';
COMMENT ON COLUMN "deck"."style_profile"."label" IS '展示名';
COMMENT ON COLUMN "deck"."style_profile"."description" IS '描述（可空）';
COMMENT ON COLUMN "deck"."style_profile"."source" IS '来源 (custom:自定义:blue/override:覆盖内置:orange)';
COMMENT ON COLUMN "deck"."style_profile"."design_contract" IS '预设的 DesignContract（JSON）';
COMMENT ON COLUMN "deck"."style_profile"."style_prompt" IS '注入生成的风格提示词片段';
COMMENT ON COLUMN "deck"."style_profile"."owner_id" IS '归属 owner HASN ID（owner 隔离键）';
COMMENT ON COLUMN "deck"."style_profile"."rev" IS '单调版本（乐观并发 + 同步水位）';
COMMENT ON COLUMN "deck"."style_profile"."created_time" IS '创建时间';
COMMENT ON COLUMN "deck"."style_profile"."updated_time" IS '更新时间';
COMMENT ON COLUMN "deck"."style_profile"."deleted_time" IS '软删时间（非空=已删）';
