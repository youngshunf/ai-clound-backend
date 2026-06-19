-- =====================================================
-- 桌面端潜行会议副驾（会议副驾）云端权威：copilot_session 根表
-- 独立 PG schema=hasn_copilot（ADR：AI-Native 应用命名空间与目录）
-- 副驾会话业务元数据，与「工作会话」session_id 1:1（任务系统 session_kind=task / summary_only）；
--   实时转写/建议都在工作会话内（直连 hermes runtime_session），不进 conversation；本表只存副驾特有属性 + 投影落点。
-- 主键约定：bigint 自增（对齐 fba id_key，codegen 生成 model 即用）；
--   离线起会的客户端去重靠 session_id UNIQUE 上行 upsert（端云 id 经本地 server_id 映射，不依赖 PK）。
-- 共享表（身份 public.hasn_humans/hasn_agents）跨 schema 全限定引用。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_copilot/copilot_session.sql --app hasn_copilot --schema hasn_copilot --execute
-- 设计事实源：docs/hasn-node设计文档/桌面端潜行会议副驾/01-桌面端潜行会议副驾总体设计.md §8.4.2
-- scope=app(owner)；owner 硬隔离强制 owner_hasn_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

CREATE TABLE "hasn_copilot"."copilot_session" (
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
-- 注：updated_time 可空（无 NOT NULL/DEFAULT），对齐 fba DateTimeMixin（插入时不写、首次更新前为 NULL，onupdate 自动刷新）。

CREATE INDEX        "idx_copilot_session_owner" ON "hasn_copilot"."copilot_session" ("owner_hasn_id", "created_time" DESC);
CREATE UNIQUE INDEX "idx_copilot_session_sid"   ON "hasn_copilot"."copilot_session" ("session_id");

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
