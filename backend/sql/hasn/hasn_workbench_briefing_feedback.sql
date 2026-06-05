-- =====================================================
-- HASN 工作台简报反馈（云端权威）
-- 主人对关注项的 dismiss/已处理 记录；喂下次简报去重/降权
-- app 写（owner 隔离）
-- 设计事实源：docs/hasn-node设计文档/13-工作台/04-...设计.md §4.3/§8
-- =====================================================
CREATE TABLE "public"."hasn_workbench_briefing_feedback" (
  "id"            bigserial PRIMARY KEY,
  "owner_hasn_id" varchar(40)  NOT NULL,
  "period"        varchar(10)  NOT NULL,
  "item_id"       varchar(64)  NOT NULL,
  "action"        varchar(32)  NOT NULL DEFAULT 'dismiss',
  "source_ref"    varchar(256),
  "note"          text,
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE INDEX "idx_workbench_briefing_feedback_owner_period"
  ON "public"."hasn_workbench_briefing_feedback" ("owner_hasn_id", "period");

COMMENT ON TABLE "public"."hasn_workbench_briefing_feedback" IS 'HASN 工作台简报反馈（云端权威）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."owner_hasn_id" IS '主人 HASN ID（owner 隔离键）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."period" IS '所属简报周期 YYYY-MM-DD';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."item_id" IS '被标记的关注项 item_id';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."action" IS '反馈动作 (dismiss:已知道:gray/done:已处理:green)';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."source_ref" IS '关注项溯源 source.ref（去重用）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."note" IS '备注（可空）';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_workbench_briefing_feedback"."updated_time" IS '更新时间';
