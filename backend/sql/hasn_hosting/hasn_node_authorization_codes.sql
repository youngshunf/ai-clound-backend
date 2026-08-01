-- =====================================================
-- 无头 hasn-node 托管 · 设备授权码表（H2）
-- 契约：docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md §2.1
-- 明文码只在签发时经服务端流向 hosting-agent，落库只留 sha256(明文) 十六进制。
-- =====================================================
CREATE TABLE "public"."hasn_node_authorization_codes" (
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
  "updated_time"  timestamptz(6),
  CONSTRAINT "uq_hasn_node_auth_codes_code_hash" UNIQUE ("code_hash")
);

CREATE INDEX "idx_hasn_node_auth_codes_node_status" ON "public"."hasn_node_authorization_codes" ("node_id", "status");
CREATE INDEX "idx_hasn_node_auth_codes_expires" ON "public"."hasn_node_authorization_codes" ("expires_at");

COMMENT ON TABLE "public"."hasn_node_authorization_codes" IS '云端节点设备授权码';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."code_hash" IS '授权码 sha256 十六进制，明文不入库';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."user_id" IS '平台用户 ID';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."owner_hasn_id" IS '主人 HASN ID';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."node_id" IS '预分配的 hasn_nodes.node_id';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."purpose" IS '用途 (create:首次创建:blue/reauthorize:重新授权:orange)';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."expires_at" IS '过期时刻（签发 + 5 分钟）';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."consumed_at" IS '兑换时刻';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."status" IS '状态 (pending:待兑换:blue/consumed:已兑换:green/expired:已过期:orange/revoked:已作废:red)';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_node_authorization_codes"."updated_time" IS '更新时间';
