-- =====================================================
-- 无头 hasn-node 托管 · 托管状态表（hosting 侧权威，H4）
-- 契约：docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md §2.2
-- 注意：status='online' 的充要条件是 Redis presence 命中，容器 running 不等于在线（D-13）。
-- =====================================================
CREATE TABLE "public"."hasn_cloud_nodes" (
  "id"                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "node_id"                 varchar(40) NOT NULL,
  "user_id"                 int8 NOT NULL,
  "owner_hasn_id"           varchar(40) NOT NULL,
  "host"                    varchar(64) NOT NULL,
  "container_ref"           varchar(128),
  "status"                  varchar(16) NOT NULL DEFAULT 'provisioning',
  "failure_reason"          varchar(32),
  "failure_detail"          text,
  "image_version"           varchar(64),
  "image_digest"            varchar(128),
  "credential_session_uuid" varchar(64),
  "retain_until"            timestamptz(6),
  "last_backup_at"          timestamptz(6),
  "online_since"            timestamptz(6),
  "created_time"            timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"            timestamptz(6),
  CONSTRAINT "uq_hasn_cloud_nodes_node_id" UNIQUE ("node_id")
);

CREATE INDEX "idx_hasn_cloud_nodes_owner" ON "public"."hasn_cloud_nodes" ("owner_hasn_id");
CREATE INDEX "idx_hasn_cloud_nodes_status" ON "public"."hasn_cloud_nodes" ("status");
CREATE INDEX "idx_hasn_cloud_nodes_host" ON "public"."hasn_cloud_nodes" ("host");

COMMENT ON TABLE "public"."hasn_cloud_nodes" IS '云端托管节点状态';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."node_id" IS '对应 hasn_nodes.node_id';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."user_id" IS '平台用户 ID';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."owner_hasn_id" IS '主人 HASN ID';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."host" IS '承载宿主标识（MVP 单宿主也必须落值）';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."container_ref" IS 'hosting-agent 侧容器标识';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."status" IS '状态 (provisioning:创建中:blue/starting:启动中:cyan/online:在线:green/stopped:已停止:gray/updating:更新中:orange/failed:失败:red/deleting:删除中:orange/deleted:已删除:gray)';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."failure_reason" IS '失败原因码（subscription_invalid/authorization_code_expired/authorization_code_consumed/credential_invalid/resource_exhausted/image_pull_failed/container_crashed/daemon_not_online/internal_error）';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."failure_detail" IS '人可读失败详情';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."image_version" IS '镜像版本号';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."image_digest" IS '镜像 digest（以 digest 为准，不信 tag）';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."credential_session_uuid" IS '设备凭据所在 JWT session，用于单独吊销';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."retain_until" IS '订阅到期后的数据保留截止时刻';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."last_backup_at" IS '最近一次卷备份时刻，NULL 表示尚无备份';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."online_since" IS '本次上线起始时刻';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."updated_time" IS '更新时间';
