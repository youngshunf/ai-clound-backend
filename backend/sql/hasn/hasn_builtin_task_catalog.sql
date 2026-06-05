-- =====================================================
-- HASN 官方内置任务目录（云端权威）
-- 官方维护的定时任务定义；daemon 拉取后幂等播种成绑主脑的本地任务
-- 设计事实源：docs/hasn-node设计文档/13-工作台/04-...设计.md §3.3
-- =====================================================
CREATE TABLE "public"."hasn_builtin_task_catalog" (
  "id"               bigserial PRIMARY KEY,
  "builtin_key"      varchar(64)  NOT NULL,
  "name"             varchar(128) NOT NULL,
  "description"      text,
  "schedule_type"    varchar(16)  NOT NULL DEFAULT 'cron',
  "schedule_config"  jsonb        NOT NULL DEFAULT '{"expr":"0 8 * * *"}',
  "skill_bundle"     varchar(128),
  "system_prompt"    text,
  "enabled"          boolean      NOT NULL DEFAULT true,
  "min_node_version" varchar(32),
  "revision"         bigint       NOT NULL DEFAULT 1,
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6),
  CONSTRAINT "uq_builtin_task_catalog_key" UNIQUE ("builtin_key")
);

CREATE INDEX "idx_builtin_task_catalog_enabled" ON "public"."hasn_builtin_task_catalog" ("enabled");

COMMENT ON TABLE "public"."hasn_builtin_task_catalog" IS 'HASN 官方内置任务目录（云端权威）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."builtin_key" IS '内置任务全局唯一键（如 daily_briefing）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."name" IS '任务名称';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."description" IS '任务说明';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."schedule_type" IS '调度类型 (cron:Cron表达式:blue/interval:间隔:green/once:单次:gray)';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."schedule_config" IS '调度配置 JSONB（cron 用 {"expr":"0 8 * * *"}）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."skill_bundle" IS '执行技能包（如 huanxing/workbench-briefing）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."system_prompt" IS '系统提示词（约束输出统一格式）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."enabled" IS '全局上/下线开关';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."min_node_version" IS '要求的最低客户端版本（可空）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."revision" IS '目录版本号（变化时 daemon 重拉）';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_builtin_task_catalog"."updated_time" IS '更新时间';
