-- =====================================================
-- 设计系统生成应用（app_id=designsystem）云端权威：design_system 根表
-- 独立 PG schema=hasn_designsystem（ADR-15：AI-Native 应用命名空间与目录约定）
-- 主键约定：bigint 自增（对齐 fba id_key + 姊妹应用 deck；端云 id 经本地 server_id 映射，
--   见 doc 01 §8）。设计意图 DDL（设计 §5.3）用 UUID，正式以 codegen 产物为准——取 bigint。
-- 共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets、企业 public.hasn_enterprise）
--   跨 schema 全限定引用，不在本 schema 重造。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_designsystem/design_system.sql --app hasn_designsystem --schema hasn_designsystem --execute
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/20-设计系统生成应用(自研)架构设计.md §5.3
-- scope=app(owner)/agent/open/admin；owner 隔离强制 owner_hasn_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_designsystem";

CREATE TABLE "hasn_designsystem"."design_system" (
  "id"                  bigserial      PRIMARY KEY,
  "owner_hasn_id"       varchar(64)    NOT NULL,
  "name"                varchar(128)   NOT NULL,
  "slug"                varchar(128)   NOT NULL,
  "category"            varchar(48),
  "source_kind"         varchar(32)    NOT NULL DEFAULT 'generated',
  "score"               smallint,
  "grade"               varchar(16),
  "recommend_rebuild"   boolean        NOT NULL DEFAULT false,
  "is_builtin"          boolean        NOT NULL DEFAULT false,
  "enterprise_id"       bigint,
  "current_revision_id" bigint,
  "content_hash"        varchar(128)   NOT NULL DEFAULT '',
  "bound_agent_id"      varchar(40),
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6),
  "deleted_time"        timestamptz(6),
  UNIQUE ("owner_hasn_id", "slug")
);

CREATE INDEX "idx_ds_owner"      ON "hasn_designsystem"."design_system" ("owner_hasn_id") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_ds_builtin"    ON "hasn_designsystem"."design_system" ("is_builtin") WHERE "is_builtin";
CREATE INDEX "idx_ds_enterprise" ON "hasn_designsystem"."design_system" ("enterprise_id") WHERE "enterprise_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_designsystem"."design_system" IS '设计系统（云端权威）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."id" IS '主键 ID（自增 BigInt；端云经本地 server_id 映射）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."owner_hasn_id" IS '归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."name" IS '设计系统名称';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."slug" IS 'owner 维度唯一 slug（同 owner 不可重复）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."category" IS '分类（可空，如 saas/editorial/playful…）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."source_kind" IS '来源 (generated:生成:violet/imported_shadcn:shadcn导入:blue/imported_github:GitHub导入:blue/imported_screenshot:截图导入:cyan/seed:官方内置:green)';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."score" IS '当前版评分 0-100（token 契约评分）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."grade" IS '等级 (excellent:优秀:green/good:良好:blue/fair:一般:orange/poor:较差:red)';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."recommend_rebuild" IS '是否建议重建（评分过低）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."is_builtin" IS '是否官方内置（seed，跨 owner 只读可见）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."enterprise_id" IS '归属企业 ID（null=个人；非空=企业私有，引用 public.hasn_enterprise）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."current_revision_id" IS '当前版 revision.id（指向最新 revision）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."content_hash" IS '当前版内容 hash（供同步 revision diff）';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_designsystem"."design_system"."deleted_time" IS '软删时间（非空=已删，不物理删以便同步感知）';
