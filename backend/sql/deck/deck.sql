-- =====================================================
-- 演示文稿系统（模块 17，app_id=deck）云端权威：deck 根表
-- 独立 PG schema=deck（ADR：AI-Native 应用命名空间与目录）
-- 表名去冗余前缀 + schema 限定：deck.deck / deck.page / deck.asset / deck.revision / deck.style_profile
-- 共享表（资产 public.hasn_assets、身份 public.hasn_humans/hasn_agents）跨 schema 全限定引用
-- 生成：uv run fba codegen generate --sql-file backend/sql/deck/deck.sql --app deck --schema deck --execute
-- 设计事实源：docs/hasn-node设计文档/17-演示文稿系统/01-数据模型.md §3/§9
-- scope=app(owner)/agent；owner 隔离强制 owner_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "deck";

CREATE TABLE "deck"."deck" (
  "id"               uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "owner_id"         varchar(40)    NOT NULL,
  "title"            varchar(255)   NOT NULL,
  "topic"            text,
  "status"           varchar(20)    NOT NULL DEFAULT 'draft',
  "language"         varchar(16)    NOT NULL DEFAULT 'zh',
  "outline"          jsonb,
  "design_contract"  jsonb,
  "style_profile_id" varchar(64),
  "page_count"       int            NOT NULL DEFAULT 0,
  "cover_asset_id"   varchar(64),
  "source"           varchar(16)    NOT NULL DEFAULT 'manual',
  "rev"              bigint         NOT NULL DEFAULT 1,
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6),
  "deleted_time"     timestamptz(6)
);

CREATE INDEX "idx_deck_owner_status" ON "deck"."deck" ("owner_id", "status") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "deck"."deck" IS '演示文稿（云端权威）';
COMMENT ON COLUMN "deck"."deck"."id" IS '主键 UUID';
COMMENT ON COLUMN "deck"."deck"."owner_id" IS '归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）';
COMMENT ON COLUMN "deck"."deck"."title" IS '标题';
COMMENT ON COLUMN "deck"."deck"."topic" IS '原始主题/brief（生成来源，可空）';
COMMENT ON COLUMN "deck"."deck"."status" IS '状态 (draft:草稿:blue/generating:生成中:orange/ready:就绪:green/archived:归档:gray)';
COMMENT ON COLUMN "deck"."deck"."language" IS '主语言（zh/en…，影响生成）';
COMMENT ON COLUMN "deck"."deck"."outline" IS '大纲 OutlineItem[]（JSON）';
COMMENT ON COLUMN "deck"."deck"."design_contract" IS '统一视觉契约 DesignContract（JSON）';
COMMENT ON COLUMN "deck"."deck"."style_profile_id" IS '引用的 StyleProfile（可空=自定义）';
COMMENT ON COLUMN "deck"."deck"."page_count" IS '页数冗余计数（= page 行数，便于列表）';
COMMENT ON COLUMN "deck"."deck"."cover_asset_id" IS '封面缩略图资产 id（引用 public.hasn_assets.asset_id，可空）';
COMMENT ON COLUMN "deck"."deck"."source" IS '来源 (agent:分身生成:violet/manual:手建:gray/imported:导入:blue)';
COMMENT ON COLUMN "deck"."deck"."rev" IS '单调版本（乐观并发 + 同步水位，每次写 +1）';
COMMENT ON COLUMN "deck"."deck"."created_time" IS '创建时间';
COMMENT ON COLUMN "deck"."deck"."updated_time" IS '更新时间';
COMMENT ON COLUMN "deck"."deck"."deleted_time" IS '软删时间（非空=已删，不物理删以便同步感知）';
