-- =====================================================
-- 演示文稿系统（模块 17，app_id=deck）云端权威：deck 根表
-- 独立 PG schema=hasn_deck（ADR：AI-Native 应用命名空间与目录）
-- 表名去冗余前缀 + schema 限定：hasn_deck.deck / hasn_deck.page / hasn_deck.asset / hasn_deck.revision / hasn_deck.style_profile
-- 共享表（资产 public.hasn_assets、身份 public.hasn_humans/hasn_agents）跨 schema 全限定引用
-- 主键约定：bigint 自增（对齐 fba `id_key`，codegen 生成 model 即用，无需 UUID 主键基座）；
--   端云 id 不一致，本地 SQLite(ULID) ↔ 云端 bigint 经本地 `server_id` 映射（见 doc 01 §8），故无需 `uid` 列。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_deck/deck.sql --app hasn_deck --schema hasn_deck --execute
-- 设计事实源：docs/hasn-node设计文档/17-演示文稿系统/01-数据模型.md §3/§9
-- scope=app(owner)/agent；owner 隔离强制 owner_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_deck";

CREATE TABLE "hasn_deck"."deck" (
  "id"               bigserial      PRIMARY KEY,
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

CREATE INDEX "idx_deck_owner_status" ON "hasn_deck"."deck" ("owner_id", "status") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "hasn_deck"."deck" IS '演示文稿（云端权威）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."id" IS '主键 ID（自增 BigInt；端云经本地 server_id 映射）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."owner_id" IS '归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."title" IS '标题';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."topic" IS '原始主题/brief（生成来源，可空）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."status" IS '状态 (draft:草稿:blue/generating:生成中:orange/ready:就绪:green/archived:归档:gray)';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."language" IS '主语言（zh/en…，影响生成）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."outline" IS '大纲 OutlineItem[]（JSON）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."design_contract" IS '统一视觉契约 DesignContract（JSON）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."style_profile_id" IS '引用的 StyleProfile slug（可空=自定义）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."page_count" IS '页数冗余计数（= page 行数，便于列表）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."cover_asset_id" IS '封面缩略图资产 id（引用 public.hasn_assets.asset_id，可空）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."source" IS '来源 (agent:分身生成:violet/manual:手建:gray/imported:导入:blue)';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."rev" IS '单调版本（乐观并发 + 同步水位，每次写 +1）';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_deck"."hasn_deck"."deleted_time" IS '软删时间（非空=已删，不物理删以便同步感知）';
