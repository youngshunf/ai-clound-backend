-- 技能市场同步日志表
CREATE TABLE IF NOT EXISTS "public"."marketplace_sync_log" (
  "id" bigserial PRIMARY KEY,
  "sync_type" varchar(20) NOT NULL,
  "status" varchar(20) NOT NULL,
  "items_synced" int4 DEFAULT 0,
  "items_failed" int4 DEFAULT 0,
  "error_message" text,
  "git_commit_before" varchar(64),
  "git_commit_after" varchar(64),
  "started_at" timestamptz(6) NOT NULL,
  "completed_at" timestamptz(6),
  "created_time" timestamptz(6) DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_sync_log_type ON marketplace_sync_log(sync_type, created_time DESC);

-- 字段注释
COMMENT ON TABLE "public"."marketplace_sync_log" IS '技能市场同步日志表';
COMMENT ON COLUMN "public"."marketplace_sync_log"."id" IS '主键ID';
COMMENT ON COLUMN "public"."marketplace_sync_log"."sync_type" IS '同步类型 (github:GitHub同步:blue/clawhub:ClawHub同步:green)';
COMMENT ON COLUMN "public"."marketplace_sync_log"."status" IS '同步状态 (success:成功:green/failed:失败:red/partial:部分成功:orange)';
COMMENT ON COLUMN "public"."marketplace_sync_log"."items_synced" IS '成功同步数量';
COMMENT ON COLUMN "public"."marketplace_sync_log"."items_failed" IS '失败数量';
COMMENT ON COLUMN "public"."marketplace_sync_log"."error_message" IS '错误信息';
COMMENT ON COLUMN "public"."marketplace_sync_log"."git_commit_before" IS '同步前的 commit hash';
COMMENT ON COLUMN "public"."marketplace_sync_log"."git_commit_after" IS '同步后的 commit hash';
COMMENT ON COLUMN "public"."marketplace_sync_log"."started_at" IS '开始时间';
COMMENT ON COLUMN "public"."marketplace_sync_log"."completed_at" IS '完成时间';
COMMENT ON COLUMN "public"."marketplace_sync_log"."created_time" IS '创建时间';
