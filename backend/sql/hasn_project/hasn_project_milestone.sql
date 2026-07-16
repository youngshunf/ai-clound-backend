-- =====================================================
-- 平台项目里程碑轻表（模块 14 doc38 §12.3，schema=hasn_project）
-- v2 项目管理增量：里程碑「只是业务状态标记」——无依赖边、无门控、无产出闸（第四条铁律）。
--   想给里程碑加依赖的那一刻，就该改用场景工作流（doc11）挂进项目。
-- 与 plan_milestone 是「不同物」：本表=业务交付节点（可关联产物），plan_milestone=计划内部阶段（永不关联产物）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_project/hasn_project_milestone.sql --app hasn_project --schema hasn_project --execute
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §12.3/§12.4
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_project";

CREATE TABLE "hasn_project"."hasn_project_milestone" (
  "id"           bigserial      PRIMARY KEY,
  "project_id"   uuid           NOT NULL REFERENCES "hasn_project"."hasn_project"("id") ON DELETE CASCADE,
  "name"         varchar(200)   NOT NULL,
  "due_time"     timestamptz(6),
  "status"       varchar(16)    NOT NULL DEFAULT 'pending',
  "artifact_ref" text,
  "sort"         int            NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6)
);

CREATE INDEX "idx_hasn_project_milestone_project" ON "hasn_project"."hasn_project_milestone" ("project_id", "sort");

COMMENT ON TABLE  "hasn_project"."hasn_project_milestone" IS '平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."id" IS '主键 ID（自增 BigInt；里程碑不进 URI，无需 UUID）';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."project_id" IS '所属项目 id（hasn_project.id，物理 FK 级联删）';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."name" IS '里程碑名';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."due_time" IS '到期时间（可空；逾期由读时按当前时间派生，不落库状态）';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."status" IS '状态 (pending:待完成:blue/done:已完成:green)';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."artifact_ref" IS '关联产物引用（hasn:// 资源或 artifact_id，可空；业务交付节点的锚，doc38 §12.4）';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."sort" IS '排序（里程碑轨横向次序）';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_project"."hasn_project_milestone"."updated_time" IS '更新时间';
