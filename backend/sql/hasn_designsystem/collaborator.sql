-- =====================================================
-- 设计系统生成应用（app_id=designsystem）：collaborator 协作分身绑定表
-- 对齐 DECKBIND：一套设计系统可绑定一/多个协作分身（"设计系统专家"），
-- 经协作栏自然语言指令派给分身改 tokens 出新 revision。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_designsystem/collaborator.sql --app hasn_designsystem --schema hasn_designsystem --execute
-- 设计事实源：设计 §5.3 / §9.4
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_designsystem";

CREATE TABLE "hasn_designsystem"."collaborator" (
  "id"               bigserial      PRIMARY KEY,
  "design_system_id" bigint         NOT NULL,
  "agent_hasn_id"    varchar(64)    NOT NULL,
  "added_by"         varchar(64)    NOT NULL,
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6)
);

CREATE UNIQUE INDEX "uq_ds_collaborator" ON "hasn_designsystem"."collaborator" ("design_system_id", "agent_hasn_id");

COMMENT ON TABLE  "hasn_designsystem"."collaborator" IS '设计系统协作分身绑定（对齐 DECKBIND）';
COMMENT ON COLUMN "hasn_designsystem"."collaborator"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "hasn_designsystem"."collaborator"."design_system_id" IS '所属 design_system.id';
COMMENT ON COLUMN "hasn_designsystem"."collaborator"."agent_hasn_id" IS '协作分身 HASN ID（a_* 分身）';
COMMENT ON COLUMN "hasn_designsystem"."collaborator"."added_by" IS '添加者 HASN ID（owner）';
COMMENT ON COLUMN "hasn_designsystem"."collaborator"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_designsystem"."collaborator"."updated_time" IS '更新时间';
