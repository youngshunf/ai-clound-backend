-- =====================================================
-- HASN 主人工作台偏好表（主脑指针 + 每日简报偏好）
-- 云端权威，daemon 本地镜像；每个 owner 一行
-- 设计事实源：docs/hasn-node设计文档/13-工作台/04-...设计.md §2.2
-- =====================================================
CREATE TABLE "public"."hasn_owner_workbench_pref" (
  "id"                bigserial PRIMARY KEY,
  "owner_hasn_id"     varchar(40) NOT NULL,
  "primary_agent_id"  varchar(40),
  "briefing_enabled"  boolean NOT NULL DEFAULT true,
  "briefing_time"     varchar(5) NOT NULL DEFAULT '08:00',
  "briefing_sources"  jsonb NOT NULL DEFAULT '["task","social","app","plan"]',
  "created_time"      timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"      timestamptz(6),
  CONSTRAINT "uq_owner_workbench_pref_owner" UNIQUE ("owner_hasn_id")
);

CREATE INDEX "idx_owner_workbench_pref_owner" ON "public"."hasn_owner_workbench_pref" ("owner_hasn_id");

COMMENT ON TABLE "public"."hasn_owner_workbench_pref" IS 'HASN 主人工作台偏好（主脑指针 + 每日简报偏好）';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."owner_hasn_id" IS '主人 hasn_id（每人一行，唯一）';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."primary_agent_id" IS '主脑分身 hasn_id（空=回落首个分身）';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."briefing_enabled" IS '每日简报开关';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."briefing_time" IS '简报生成时刻（本地时区 HH:MM）';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."briefing_sources" IS '简报数据源开关（JSONB 数组：task/social/app/plan）';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_owner_workbench_pref"."updated_time" IS '更新时间';
