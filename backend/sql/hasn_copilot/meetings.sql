-- =====================================================
-- 会议副驾 v5 云端会议结果域：meetings 主档（云端权威，hasn://meeting/{id} 的权威 id 源）
-- 独立 PG schema=hasn_copilot（ADR：AI-Native 应用命名空间与目录）
-- 会议结果容器（§6.0.7）：会议是 owner 私有的一等结果资源，daemon 本地镜像 meetings_mirror
--   以本表 id（UUID）为身份键做 local_first 缓存；过程留本机、结果存云端。
-- 主键约定：UUID（gen_random_uuid），非 bigint——daemon 深链 hasn://meeting/{id} 直接用本 id，
--   遵铁律「本地 ID 永不上 URI / 云端权威 ID 才是打开依据」。
-- 时间字段两类：会议起止 started_at/ended_at/duration_ms 用 bigint（unix 秒/毫秒，daemon 收发整数）；
--   created_time/updated_time 用 timestamptz 作审计（对齐 fba DateTimeMixin）。
-- 共享表（身份 public.hasn_humans/hasn_agents）跨 schema 全限定引用。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_copilot/meetings.sql --app hasn_copilot --schema hasn_copilot --execute
-- 设计事实源：docs/hasn-node设计文档/会议副驾/实施/03-v5原型1比1还原UI实施方案.md §6.0.7
-- scope=app(owner)；owner 硬隔离强制 owner_hasn_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_copilot";

CREATE TABLE "hasn_copilot"."meetings" (
  "id"                          uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "owner_hasn_id"               varchar(64)    NOT NULL,
  "enterprise_id"               varchar(64),
  "agent_hasn_id"               varchar(64),
  "session_id"                  varchar(64)    NOT NULL,
  "node_id"                     varchar(64),
  "title"                       varchar(256)   NOT NULL DEFAULT '',
  "scene"                       varchar(32),
  "started_at"                  bigint,
  "ended_at"                    bigint,
  "duration_ms"                 bigint,
  "status"                      varchar(16)    NOT NULL DEFAULT 'active',
  "record_version"              integer        NOT NULL DEFAULT 0,
  "speaker_annotation_revision" varchar(64),
  "participants_json"           jsonb          NOT NULL DEFAULT '[]',
  "minutes_state"               varchar(16)    NOT NULL DEFAULT 'none',
  "minutes_version"             integer        NOT NULL DEFAULT 0,
  "stats_json"                  jsonb          NOT NULL DEFAULT '{}',
  "shared_media_json"           jsonb          NOT NULL DEFAULT '[]',
  "created_time"                timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"                timestamptz(6)
);
-- 注：updated_time 可空（无 NOT NULL/DEFAULT），对齐 fba DateTimeMixin（插入不写、onupdate 自动刷新）。

CREATE UNIQUE INDEX "idx_meetings_owner_session" ON "hasn_copilot"."meetings" ("owner_hasn_id", "session_id");
CREATE INDEX        "idx_meetings_owner_updated" ON "hasn_copilot"."meetings" ("owner_hasn_id", "updated_time" DESC);

COMMENT ON TABLE  "hasn_copilot"."meetings" IS '会议副驾会议主档（云端权威结果容器）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."id" IS '会议权威 ID（UUID；hasn://meeting/{id} 的 {id} 段，daemon 深链/打开依据）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."owner_hasn_id" IS '归属 owner HASN ID（owner 隔离键，所有查询强制带；引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."enterprise_id" IS '所属企业 ID（团队协作预留，首发恒 NULL）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."agent_hasn_id" IS '绑定协作分身 HASN ID（owner 名下 a_* 分身）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."session_id" IS '处理工作会话 id（任务系统 session；create 按 (owner,session_id) upsert）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."node_id" IS '采集设备节点 id';
COMMENT ON COLUMN "hasn_copilot"."meetings"."title" IS '会议标题（可由分身自动命名）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."scene" IS '场景 (meeting:会议:blue/interview:面试:violet/call:通话:green/lecture:课堂:amber)';
COMMENT ON COLUMN "hasn_copilot"."meetings"."started_at" IS '会议开始时间（unix 秒，整数）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."ended_at" IS '会议结束时间（unix 秒，整数）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."duration_ms" IS '会议时长（毫秒）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."status" IS '状态 (active:进行中:green/ended:已结束:blue/finalized:已定稿:gray)';
COMMENT ON COLUMN "hasn_copilot"."meetings"."record_version" IS '转写记录定稿版本号（segments 幂等上推时 bump）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."speaker_annotation_revision" IS '说话人标注修订号（说话人定稿快照对应版本）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."participants_json" IS '说话人定稿快照 JSON 数组（[{cluster_id,speaker_label,...}]）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."minutes_state" IS '纪要状态 (none:未生成:gray/queued:排队中:amber/ready:已就绪:green/failed:失败:red)';
COMMENT ON COLUMN "hasn_copilot"."meetings"."minutes_version" IS '纪要当前版本号（纪要写入时提升）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."stats_json" IS '会议统计 JSON 对象（时长/发言分布/要点数等）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."shared_media_json" IS '升格媒体清单 JSON 数组（owner 逐件勾选升格的音视频/截图）';
COMMENT ON COLUMN "hasn_copilot"."meetings"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_copilot"."meetings"."updated_time" IS '更新时间';
