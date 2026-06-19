-- =====================================================
-- 桌面端潜行会议副驾（会议副驾）P2 云端数据底座建表迁移
-- schema hasn_copilot：copilot_session（会话元数据）+ copilot_preference（owner 级偏好单行）
-- 设计事实源：docs/hasn-node设计文档/桌面端潜行会议副驾/01-桌面端潜行会议副驾总体设计.md §8.4.2 / §8.5
--
-- ⚠️ 生产部署不走 deploy 自动跑 SQL，由福仔手动 psql -d <库> -f 本文件执行。
-- 幂等：可重复执行（IF NOT EXISTS / DROP NOT NULL 均安全）。
-- 注：updated_time 可空（对齐 fba DateTimeMixin：插入不写、首次更新前 NULL、onupdate 自动刷新）；
--   若历史已用 NOT NULL/DEFAULT 建表，本迁移末尾的 ALTER 会修正，保证与 ORM 一致。
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

-- ---------- copilot_session ----------
CREATE TABLE IF NOT EXISTS "hasn_copilot"."copilot_session" (
  "id"                         bigserial      PRIMARY KEY,
  "owner_hasn_id"              varchar(64)    NOT NULL,
  "session_id"                 varchar(64)    NOT NULL,
  "bound_agent_id"             varchar(64),
  "title"                      varchar(256)   NOT NULL DEFAULT '',
  "scene"                      varchar(32)    NOT NULL DEFAULT 'meeting',
  "response_mode"              varchar(16)    NOT NULL DEFAULT 'manual',
  "status"                     varchar(16)    NOT NULL DEFAULT 'active',
  "source_config"              jsonb          NOT NULL DEFAULT '{}',
  "projection_conversation_id" uuid,
  "projection_message_id"      uuid,
  "started_time"               timestamptz(6),
  "ended_time"                 timestamptz(6),
  "created_time"               timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"               timestamptz(6)
);

CREATE INDEX        IF NOT EXISTS "idx_copilot_session_owner" ON "hasn_copilot"."copilot_session" ("owner_hasn_id", "created_time" DESC);
CREATE UNIQUE INDEX IF NOT EXISTS "idx_copilot_session_sid"   ON "hasn_copilot"."copilot_session" ("session_id");

COMMENT ON TABLE  "hasn_copilot"."copilot_session" IS '会议副驾会话元数据（云端权威）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."id" IS '主键 ID（自增 BigInt；端云经本地 server_id 映射）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."owner_hasn_id" IS '归属 owner HASN ID（owner 隔离键，所有查询强制带；引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."session_id" IS '工作会话 id（任务系统 session_kind=task/summary_only，直连 hermes runtime_session；转写/建议都在此会话内，不在 conversation）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."bound_agent_id" IS '协作分身 HASN ID（owner 名下 a_* 分身，会话级快照；null=未绑定）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."title" IS '会议标题（可由分身自动命名）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."scene" IS '场景 (meeting:会议:blue/interview:面试:violet/call:通话:green/lecture:课堂:amber)';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."response_mode" IS '应答模式 (auto:自动应答:green/manual:点了才答:blue/transcribe_only:仅转写:gray)';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."status" IS '状态 (active:进行中:green/ended:已结束:gray)';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."source_config" IS '采集快照 JSON（system_audio/mic/devices/stealth 三档开关）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."projection_conversation_id" IS '完成投影到的主 IM 会话 id（null=未投影）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."projection_message_id" IS '投影的那条卡片消息 id（点击→导航回工作会话详情）';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."started_time" IS '会议开始时间';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."ended_time" IS '会议结束时间';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."copilot_session"."updated_time" IS '更新时间';

-- ---------- copilot_preference ----------
CREATE TABLE IF NOT EXISTS "hasn_copilot"."copilot_preference" (
  "owner_hasn_id"         varchar(64)    PRIMARY KEY,
  "default_agent_id"      varchar(64),
  "default_response_mode" varchar(16)    NOT NULL DEFAULT 'manual',
  "auto_summary"          boolean        NOT NULL DEFAULT true,
  "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"          timestamptz(6)
);

COMMENT ON TABLE  "hasn_copilot"."copilot_preference" IS '会议副驾 owner 级偏好（单行 per owner，云端权威）';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."owner_hasn_id" IS 'owner HASN ID（主键，单行 per owner；引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."default_agent_id" IS '默认协作分身（首次绑定写入；新会话默认用它，§8.5）';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."default_response_mode" IS '默认应答模式 (auto:自动应答:green/manual:点了才答:blue/transcribe_only:仅转写:gray)';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."auto_summary" IS '会后是否自动生成纪要产物';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."copilot_preference"."updated_time" IS '更新时间';

-- ---------- updated_time 修正（对齐 fba DateTimeMixin：可空、无插入默认） ----------
ALTER TABLE "hasn_copilot"."copilot_session"    ALTER COLUMN "updated_time" DROP NOT NULL;
ALTER TABLE "hasn_copilot"."copilot_session"    ALTER COLUMN "updated_time" DROP DEFAULT;
ALTER TABLE "hasn_copilot"."copilot_preference" ALTER COLUMN "updated_time" DROP NOT NULL;
ALTER TABLE "hasn_copilot"."copilot_preference" ALTER COLUMN "updated_time" DROP DEFAULT;
