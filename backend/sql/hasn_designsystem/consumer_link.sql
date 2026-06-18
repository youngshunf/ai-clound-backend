-- =====================================================
-- 设计系统生成应用（app_id=designsystem）：consumer_link 下游消费登记表
-- 可选，用于"换系统重渲染"追踪：哪个 deck/网站/创作引入了哪套设计系统的哪一版。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_designsystem/consumer_link.sql --app hasn_designsystem --schema hasn_designsystem --execute
-- 设计事实源：设计 §5.3
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_designsystem";

CREATE TABLE "hasn_designsystem"."consumer_link" (
  "id"                bigserial      PRIMARY KEY,
  "design_system_id"  bigint         NOT NULL,
  "consumer_app"      varchar(32)    NOT NULL,
  "consumer_ref"      varchar(128)   NOT NULL,
  "bound_revision_id" bigint,
  "created_time"      timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"      timestamptz(6)
);

CREATE UNIQUE INDEX "uq_ds_consumer_link" ON "hasn_designsystem"."consumer_link" ("consumer_app", "consumer_ref");
CREATE INDEX "idx_ds_consumer_ds" ON "hasn_designsystem"."consumer_link" ("design_system_id");

COMMENT ON TABLE  "hasn_designsystem"."consumer_link" IS '设计系统下游消费登记（换系统重渲染追踪）';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."design_system_id" IS '所属 design_system.id';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."consumer_app" IS '消费方 (deck:演示文稿:violet/publish:网站发布:blue/creator:创作:cyan)';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."consumer_ref" IS '消费方资源 id（如 deck_id）';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."bound_revision_id" IS '绑定的 revision.id（消费的具体版本，可空）';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_designsystem"."consumer_link"."updated_time" IS '更新时间';
