-- =====================================================
-- HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试，doc15 §8.1 / A-P1）
-- 仅承载 A 类（hasn.* 云端/本地工具）的 ask 挂起态；B 类 Hermes 原生命令不落库。
-- =====================================================
CREATE TABLE "public"."hasn_agent_approval_requests" (
  "id"               bigserial PRIMARY KEY,
  "request_id"       varchar(40) NOT NULL,
  "agent_hasn_id"    varchar(40) NOT NULL,
  "owner_hasn_id"    varchar(40) NOT NULL,
  "tool_name"        varchar(128) NOT NULL,
  "args_hash"        varchar(64) NOT NULL,
  "args_digest"      jsonb NOT NULL DEFAULT '{}',
  "capability_keys"  jsonb NOT NULL DEFAULT '[]',
  "description"      varchar(500) NOT NULL DEFAULT '',
  "status"           varchar(16) NOT NULL DEFAULT 'pending',
  "grant_scope"      varchar(8),
  "ticket_jti"       varchar(40),
  "decided_time"     timestamptz(6),
  "expires_time"     timestamptz(6) NOT NULL,
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6),
  CONSTRAINT "uq_hasn_agent_approval_requests_request" UNIQUE ("request_id")
);

CREATE INDEX "idx_hasn_agent_approval_requests_agent_status" ON "public"."hasn_agent_approval_requests" ("agent_hasn_id", "status");
CREATE INDEX "idx_hasn_agent_approval_requests_owner" ON "public"."hasn_agent_approval_requests" ("owner_hasn_id");
CREATE INDEX "idx_hasn_agent_approval_requests_pending_expiry" ON "public"."hasn_agent_approval_requests" ("expires_time") WHERE "status" = 'pending';

COMMENT ON TABLE "public"."hasn_agent_approval_requests" IS 'HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."request_id" IS '审批请求业务 ID（areq_{ulid}）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."agent_hasn_id" IS '发起调用的 Agent hasn_id';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."owner_hasn_id" IS '审批人（主人）hasn_id';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."tool_name" IS '被调用的工具 canonical name';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."args_hash" IS '入参 canonical JSON 的 sha256（票据绑定，防换参重放）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."args_digest" IS '入参脱敏摘要 JSON（卡片展示用，不存敏感原文）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."capability_keys" IS '触发 ask 的能力 key 列表（总是允许时据此写回 capability_modes=allow）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."description" IS '人类可读的审批描述（NLG，卡片标题/正文）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."status" IS '审批状态 (pending:待审:orange/approved:已批:green/denied:已拒:red/timeout:超时:gray/consumed:已用:blue)';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."grant_scope" IS '授权粒度 (once:本次:blue/always:总是:green)';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."ticket_jti" IS '签发的一次性票据 jti（防重放追踪）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."decided_time" IS '主人决定时间';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."expires_time" IS '审批超时时间（默认 now+600s）';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_agent_approval_requests"."updated_time" IS '更新时间';
