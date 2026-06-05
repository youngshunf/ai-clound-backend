-- =====================================================
-- HASN 工作台每日关注简报（云端权威）
-- 主脑产出的统一 BriefingDocument；owner+period 覆盖式（当日最新一份）
-- app 只读（owner 隔离）+ agent 写（经 workbench.briefing.publish MCP 工具）
-- 设计事实源：docs/hasn-node设计文档/13-工作台/04-...设计.md §4/§8
-- =====================================================
CREATE TABLE "public"."hasn_workbench_briefing" (
  "id"            bigserial PRIMARY KEY,
  "owner_hasn_id" varchar(40)  NOT NULL,
  "agent_hasn_id" varchar(40)  NOT NULL,
  "period"        varchar(10)  NOT NULL,
  "state"         varchar(16)  NOT NULL DEFAULT 'ready',
  "document_json" jsonb        NOT NULL,
  "generated_at"  timestamptz(6) NOT NULL DEFAULT now(),
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE UNIQUE INDEX "uq_workbench_briefing_owner_period"
  ON "public"."hasn_workbench_briefing" ("owner_hasn_id", "period");

COMMENT ON TABLE "public"."hasn_workbench_briefing" IS 'HASN 工作台每日关注简报（云端权威）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."owner_hasn_id" IS '主人 HASN ID（owner 隔离键）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."agent_hasn_id" IS '产出该简报的主脑 HASN ID';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."period" IS '覆盖周期 YYYY-MM-DD（主人本地日期）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."state" IS '状态 (generating:生成中:blue/ready:就绪:green/failed:失败:red)';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."document_json" IS '完整 BriefingDocument（JSONB）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."generated_at" IS '产出时间';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_workbench_briefing"."updated_time" IS '更新时间';
