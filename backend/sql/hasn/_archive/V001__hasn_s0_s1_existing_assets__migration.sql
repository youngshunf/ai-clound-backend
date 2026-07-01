-- =====================================================
-- V001__hasn_s0_s1_existing_assets__migration.sql
-- S0/S1 迁移式重构：仅补字段、索引、backfill，不删除旧 HASN 业务资产。
-- 新增表的 CREATE SQL 使用同目录单表 codegen 输入文件：
--   hasn_sync_events.sql / hasn_sync_inbox_events.sql / hasn_agent_runtime_reports.sql
--   hasn_suppressed_messages.sql / hasn_pending_intents.sql / hasn_channel_bindings.sql
--   hasn_tenant_sandboxes.sql / hasn_nodes.sql / hasn_node_bindings.sql / hasn_owner_api_keys.sql
-- =====================================================

BEGIN;

-- 1) hasn_humans: profile/policy/sync revision。
ALTER TABLE "public"."hasn_humans"
  ADD COLUMN IF NOT EXISTS "profile_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "policy_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "sync_revision" bigint NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS "idx_hasn_humans_sync_revision" ON "public"."hasn_humans" ("sync_revision");

-- 2) hasn_agents: Agent Profile / Capability Summary / runtime summary cache。
ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "display_name" varchar(100),
  ADD COLUMN IF NOT EXISTS "bio" text,
  ADD COLUMN IF NOT EXISTS "profile_json" jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS "node_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "capability_summary_json" jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS "capability_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "profile_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "policy_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "sync_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "runtime_summary_json" jsonb NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS "idx_hasn_agents_node" ON "public"."hasn_agents" ("node_id") WHERE "node_id" IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_hasn_agents_sync_revision" ON "public"."hasn_agents" ("sync_revision");

UPDATE "public"."hasn_agents"
SET
  "display_name" = COALESCE(NULLIF("display_name", ''), NULLIF("name", '')),
  "bio" = COALESCE("bio", "description"),
  "profile_json" = COALESCE("profile_json", '{}'::jsonb),
  "capability_summary_json" = COALESCE("capability_summary_json", '{}'::jsonb),
  "runtime_summary_json" = COALESCE("runtime_summary_json", '{}'::jsonb)
WHERE "display_name" IS NULL
   OR "bio" IS NULL
   OR "profile_json" IS NULL
   OR "capability_summary_json" IS NULL
   OR "runtime_summary_json" IS NULL;

-- 3) hasn_contacts: channel/revision anchors。
ALTER TABLE "public"."hasn_contacts"
  ADD COLUMN IF NOT EXISTS "source_channel_binding_id" uuid,
  ADD COLUMN IF NOT EXISTS "channel_source" varchar(30),
  ADD COLUMN IF NOT EXISTS "relation_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "sync_revision" bigint NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS "idx_contact_channel_binding" ON "public"."hasn_contacts" ("source_channel_binding_id") WHERE "source_channel_binding_id" IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_contact_sync_revision" ON "public"."hasn_contacts" ("sync_revision");
COMMENT ON COLUMN "public"."hasn_contacts"."channel_source" IS '来源渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple/manual:好友请求:gray/system:AI分身:orange)';

-- 4) hasn_conversations: owner-visible conversation view anchors。
-- Backfill rule: direct conversations use participant_a as owner/subject and participant_b as peer;
-- S2/S4 may later create per-owner conversation rows as needed without splitting conversation_id.
ALTER TABLE "public"."hasn_conversations"
  ADD COLUMN IF NOT EXISTS "owner_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "hasn_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "peer_hasn_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "sync_revision" bigint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS "deleted_at" timestamptz(6);

UPDATE "public"."hasn_conversations"
SET
  "owner_id" = COALESCE("owner_id", "participant_a_id"),
  "hasn_id" = COALESCE("hasn_id", "participant_a_id"),
  "peer_hasn_id" = COALESCE("peer_hasn_id", "participant_b_id")
WHERE "owner_id" IS NULL OR "hasn_id" IS NULL OR "peer_hasn_id" IS NULL;

CREATE INDEX IF NOT EXISTS "idx_hasn_conv_owner_hasn" ON "public"."hasn_conversations" ("owner_id", "hasn_id", "updated_time" DESC);
CREATE INDEX IF NOT EXISTS "idx_hasn_conv_peer" ON "public"."hasn_conversations" ("owner_id", "peer_hasn_id") WHERE "peer_hasn_id" IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_hasn_conv_sync_revision" ON "public"."hasn_conversations" ("sync_revision");

-- 5) hasn_messages: explicit owner_id + hasn_id ownership and dispatch/delivery separation。
-- Backfill rule: owner_id/hasn_id are inferred conservatively from existing recipient/sender fields.
-- S4 must later guarantee writes never rely on Runtime availability for delivery_status.
-- RuntimeUnavailable != MessageDeliveryFailed: backfill must never turn runtime_unavailable into rejected delivery.
ALTER TABLE "public"."hasn_messages"
  ADD COLUMN IF NOT EXISTS "owner_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "hasn_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "sender_hasn_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "recipient_hasn_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "runtime_type" varchar(30),
  ADD COLUMN IF NOT EXISTS "binding_id" varchar(40),
  ADD COLUMN IF NOT EXISTS "runtime_session_id" varchar(80),
  ADD COLUMN IF NOT EXISTS "client_message_id" varchar(80),
  ADD COLUMN IF NOT EXISTS "sync_status" varchar(20) NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS "delivery_status" varchar(20) NOT NULL DEFAULT 'delivered',
  ADD COLUMN IF NOT EXISTS "dispatch_status" varchar(30) NOT NULL DEFAULT 'not_required',
  ADD COLUMN IF NOT EXISTS "owner_copy_of_message_id" int8;

UPDATE "public"."hasn_messages" m
SET
  "sender_hasn_id" = COALESCE(m."sender_hasn_id", m."from_id"),
  "recipient_hasn_id" = COALESCE(m."recipient_hasn_id", m."to_id"),
  "client_message_id" = COALESCE(m."client_message_id", m."local_id"::text),
  "owner_id" = COALESCE(m."owner_id", a."owner_id", CASE WHEN m."to_type" = 1 THEN m."to_id" ELSE m."from_id" END),
  "hasn_id" = COALESCE(m."hasn_id", CASE WHEN m."to_type" IN (1, 2) THEN m."to_id" ELSE m."from_id" END)
FROM "public"."hasn_agents" a
WHERE a."hasn_id" = m."to_id"
  AND (m."owner_id" IS NULL OR m."hasn_id" IS NULL OR m."sender_hasn_id" IS NULL OR m."recipient_hasn_id" IS NULL OR m."client_message_id" IS NULL);

-- Messages not matching Agent rows still get a deterministic Human/Owner fallback.
UPDATE "public"."hasn_messages"
SET
  "sender_hasn_id" = COALESCE("sender_hasn_id", "from_id"),
  "recipient_hasn_id" = COALESCE("recipient_hasn_id", "to_id"),
  "client_message_id" = COALESCE("client_message_id", "local_id"::text),
  "owner_id" = COALESCE("owner_id", CASE WHEN "to_type" = 1 THEN "to_id" ELSE "from_id" END),
  "hasn_id" = COALESCE("hasn_id", CASE WHEN "to_type" IN (1, 2) THEN "to_id" ELSE "from_id" END)
WHERE "owner_id" IS NULL OR "hasn_id" IS NULL OR "sender_hasn_id" IS NULL OR "recipient_hasn_id" IS NULL OR "client_message_id" IS NULL;

CREATE INDEX IF NOT EXISTS "idx_hasn_msg_owner_inbox" ON "public"."hasn_messages" ("owner_id", "hasn_id", "id" DESC);
CREATE INDEX IF NOT EXISTS "idx_hasn_msg_dispatch_status" ON "public"."hasn_messages" ("dispatch_status", "created_time" DESC);
CREATE INDEX IF NOT EXISTS "idx_hasn_msg_client_message" ON "public"."hasn_messages" ("owner_id", "client_message_id") WHERE "client_message_id" IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_hasn_msg_owner_copy" ON "public"."hasn_messages" ("owner_copy_of_message_id") WHERE "owner_copy_of_message_id" IS NOT NULL;

-- NOT NULL enforcement is intentionally deferred until post-backfill validation in S1 execution.
-- Validation query:
--   SELECT count(*) FROM public.hasn_messages WHERE owner_id IS NULL OR hasn_id IS NULL;

COMMIT;
