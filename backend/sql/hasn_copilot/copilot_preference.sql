-- =====================================================
-- 桌面端潜行会议副驾（会议副驾）云端权威：copilot_preference 表
-- 独立 PG schema=hasn_copilot；owner 级副驾偏好（单行 per owner），
--   是「默认绑定分身」的权威来源（跨设备同步、镜像本地）。
-- bound_agent / default_agent 写入前由 service 校验该分身归本 owner（同 deck）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_copilot/copilot_preference.sql --app hasn_copilot --schema hasn_copilot --execute
-- 设计事实源：docs/hasn-node设计文档/桌面端潜行会议副驾/01-桌面端潜行会议副驾总体设计.md §8.4.2 / §8.5
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

CREATE TABLE "hasn_copilot"."copilot_preference" (
  "owner_hasn_id"         varchar(64)    PRIMARY KEY,
  "default_agent_id"      varchar(64),
  "default_response_mode" varchar(16)    NOT NULL DEFAULT 'manual',
  "auto_summary"          boolean        NOT NULL DEFAULT true,
  "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"          timestamptz(6)
);
-- 注：updated_time 可空（无 NOT NULL/DEFAULT），对齐 fba DateTimeMixin（插入时不写、首次更新前为 NULL，onupdate 自动刷新）。

COMMENT ON TABLE  "hasn_copilot"."copilot_preference" IS '会议副驾 owner 级偏好（单行 per owner，云端权威）';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."owner_hasn_id" IS 'owner HASN ID（主键，单行 per owner；引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."default_agent_id" IS '默认协作分身（首次绑定写入；新会话默认用它，§8.5）';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."default_response_mode" IS '默认应答模式 (auto:自动应答:green/manual:点了才答:blue/transcribe_only:仅转写:gray)';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."auto_summary" IS '会后是否自动生成纪要产物';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."updated_time" IS '更新时间';
