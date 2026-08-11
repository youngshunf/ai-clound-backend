-- =====================================================
-- 云端节点托管初始表补迁移
--
-- 最初三张表只通过 codegen 建表 SQL 落在开发库，没有进入生产迁移清单。
-- 后续资源规格迁移会修改 hasn_cloud_nodes，因此必须先把三张基础表幂等补齐。
-- =====================================================

BEGIN;

CREATE TABLE IF NOT EXISTS "public"."hasn_node_authorization_codes" (
  "id"            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "code_hash"     varchar(64) NOT NULL,
  "user_id"       int8 NOT NULL,
  "owner_hasn_id" varchar(40) NOT NULL,
  "node_id"       varchar(40) NOT NULL,
  "purpose"       varchar(24) NOT NULL DEFAULT 'create',
  "expires_at"    timestamptz(6) NOT NULL,
  "consumed_at"   timestamptz(6),
  "status"        varchar(16) NOT NULL DEFAULT 'pending',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_node_auth_codes_code_hash"
  ON "public"."hasn_node_authorization_codes" ("code_hash");
CREATE INDEX IF NOT EXISTS "idx_hasn_node_auth_codes_node_status"
  ON "public"."hasn_node_authorization_codes" ("node_id", "status");
CREATE INDEX IF NOT EXISTS "idx_hasn_node_auth_codes_expires"
  ON "public"."hasn_node_authorization_codes" ("expires_at");

COMMENT ON TABLE "public"."hasn_node_authorization_codes" IS '云端节点设备授权码';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."code_hash" IS '授权码 sha256 十六进制，明文不入库';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."purpose" IS '用途：create/reauthorize';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."status" IS '状态：pending/consumed/expired/revoked';

CREATE TABLE IF NOT EXISTS "public"."hasn_cloud_nodes" (
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
  "updated_time"            timestamptz(6)
);

CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_cloud_nodes_node_id"
  ON "public"."hasn_cloud_nodes" ("node_id");
CREATE INDEX IF NOT EXISTS "idx_hasn_cloud_nodes_owner"
  ON "public"."hasn_cloud_nodes" ("owner_hasn_id");
CREATE INDEX IF NOT EXISTS "idx_hasn_cloud_nodes_status"
  ON "public"."hasn_cloud_nodes" ("status");
CREATE INDEX IF NOT EXISTS "idx_hasn_cloud_nodes_host"
  ON "public"."hasn_cloud_nodes" ("host");

COMMENT ON TABLE "public"."hasn_cloud_nodes" IS '云端托管节点状态';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."node_id" IS '对应 hasn_nodes.node_id';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."host" IS '承载宿主标识';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."status" IS '节点生命周期状态';
COMMENT ON COLUMN "public"."hasn_cloud_nodes"."credential_session_uuid" IS '设备凭据所在 JWT session';

CREATE TABLE IF NOT EXISTS "public"."hasn_cloud_node_events" (
  "id"            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "cloud_node_id" uuid NOT NULL,
  "node_id"       varchar(40) NOT NULL,
  "event_type"    varchar(32) NOT NULL,
  "detail"        jsonb NOT NULL DEFAULT '{}',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE INDEX IF NOT EXISTS "idx_hasn_cloud_node_events_node"
  ON "public"."hasn_cloud_node_events" ("node_id", "created_time" DESC);
CREATE INDEX IF NOT EXISTS "idx_hasn_cloud_node_events_cloud_node"
  ON "public"."hasn_cloud_node_events" ("cloud_node_id");

COMMENT ON TABLE "public"."hasn_cloud_node_events" IS '云端托管节点事件流水';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."cloud_node_id" IS '关联 hasn_cloud_nodes.id';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."event_type" IS '节点生命周期事件类型';
COMMENT ON COLUMN "public"."hasn_cloud_node_events"."detail" IS '事件明细 JSON';

DO $$
DECLARE
  v_tables int;
BEGIN
  SELECT count(*) INTO v_tables
    FROM information_schema.tables
   WHERE table_schema = 'public'
     AND table_name IN (
       'hasn_node_authorization_codes',
       'hasn_cloud_nodes',
       'hasn_cloud_node_events'
     );
  RAISE NOTICE '[改后] 云端节点托管基础表 % 张（应为 3）', v_tables;
  IF v_tables <> 3 THEN
    RAISE EXCEPTION '云端节点托管基础表未全部创建（实际 % 张）', v_tables;
  END IF;
END $$;

COMMIT;
