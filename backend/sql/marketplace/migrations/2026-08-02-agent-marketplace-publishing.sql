CREATE TABLE IF NOT EXISTS "hasn_marketplace"."marketplace_agent_publish_request" (
  "id" bigserial PRIMARY KEY,
  "agent_hasn_id" varchar(40) NOT NULL,
  "owner_hasn_id" varchar(40) NOT NULL,
  "resource_kind" varchar(20) NOT NULL,
  "idempotency_key" varchar(128) NOT NULL,
  "asset_uri" varchar(255) NOT NULL,
  "content_hash" varchar(128) NOT NULL,
  "file_hash" varchar(64) NOT NULL,
  "resource_id" varchar(255),
  "version" varchar(50),
  "state" varchar(24) NOT NULL DEFAULT 'processing',
  "result" jsonb,
  "work_session_id" varchar(64),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  CONSTRAINT "uq_marketplace_agent_publish_request_identity"
    UNIQUE ("agent_hasn_id", "resource_kind", "idempotency_key")
);

CREATE INDEX IF NOT EXISTS "idx_marketplace_agent_publish_request_owner"
  ON "hasn_marketplace"."marketplace_agent_publish_request" ("owner_hasn_id", "created_time");

ALTER TABLE "hasn_marketplace"."marketplace_skill"
  ADD COLUMN IF NOT EXISTS "requested_visibility" varchar(20) NOT NULL DEFAULT 'private';

ALTER TABLE "hasn_marketplace"."marketplace_template"
  ADD COLUMN IF NOT EXISTS "requested_visibility" varchar(20) NOT NULL DEFAULT 'private';

COMMENT ON TABLE "hasn_marketplace"."marketplace_agent_publish_request" IS 'Agent 市场发布幂等请求';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_skill"."requested_visibility" IS
  '草稿期记录的期望可见性；审核完成前实际 visibility 恒为 private';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_template"."requested_visibility" IS
  '草稿期记录的期望可见性；审核完成前实际 visibility 恒为 private';
