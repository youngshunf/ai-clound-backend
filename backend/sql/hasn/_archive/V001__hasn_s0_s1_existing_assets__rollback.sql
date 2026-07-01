-- =====================================================
-- V001__hasn_s0_s1_existing_assets__rollback.sql
-- Rollback for S0/S1 existing-asset migration. This only removes additive S1 columns/indexes.
-- Do not run after S2/S4 writes depend on these columns without first archiving affected data.
-- =====================================================

BEGIN;

DROP INDEX IF EXISTS "public"."idx_hasn_msg_owner_copy";
DROP INDEX IF EXISTS "public"."idx_hasn_msg_client_message";
DROP INDEX IF EXISTS "public"."idx_hasn_msg_dispatch_status";
DROP INDEX IF EXISTS "public"."idx_hasn_msg_owner_inbox";
ALTER TABLE "public"."hasn_messages"
  DROP COLUMN IF EXISTS "owner_copy_of_message_id",
  DROP COLUMN IF EXISTS "dispatch_status",
  DROP COLUMN IF EXISTS "delivery_status",
  DROP COLUMN IF EXISTS "sync_status",
  DROP COLUMN IF EXISTS "client_message_id",
  DROP COLUMN IF EXISTS "runtime_session_id",
  DROP COLUMN IF EXISTS "binding_id",
  DROP COLUMN IF EXISTS "runtime_type",
  DROP COLUMN IF EXISTS "recipient_hasn_id",
  DROP COLUMN IF EXISTS "sender_hasn_id",
  DROP COLUMN IF EXISTS "hasn_id",
  DROP COLUMN IF EXISTS "owner_id";

DROP INDEX IF EXISTS "public"."idx_hasn_conv_sync_revision";
DROP INDEX IF EXISTS "public"."idx_hasn_conv_peer";
DROP INDEX IF EXISTS "public"."idx_hasn_conv_owner_hasn";
ALTER TABLE "public"."hasn_conversations"
  DROP COLUMN IF EXISTS "deleted_at",
  DROP COLUMN IF EXISTS "sync_revision",
  DROP COLUMN IF EXISTS "peer_hasn_id",
  DROP COLUMN IF EXISTS "hasn_id",
  DROP COLUMN IF EXISTS "owner_id";

DROP INDEX IF EXISTS "public"."idx_contact_sync_revision";
DROP INDEX IF EXISTS "public"."idx_contact_channel_binding";
ALTER TABLE "public"."hasn_contacts"
  DROP COLUMN IF EXISTS "sync_revision",
  DROP COLUMN IF EXISTS "relation_revision",
  DROP COLUMN IF EXISTS "channel_source",
  DROP COLUMN IF EXISTS "source_channel_binding_id";

DROP INDEX IF EXISTS "public"."idx_hasn_agents_sync_revision";
DROP INDEX IF EXISTS "public"."idx_hasn_agents_node";
ALTER TABLE "public"."hasn_agents"
  DROP COLUMN IF EXISTS "runtime_summary_json",
  DROP COLUMN IF EXISTS "sync_revision",
  DROP COLUMN IF EXISTS "policy_revision",
  DROP COLUMN IF EXISTS "profile_revision",
  DROP COLUMN IF EXISTS "capability_revision",
  DROP COLUMN IF EXISTS "capability_summary_json",
  DROP COLUMN IF EXISTS "node_id",
  DROP COLUMN IF EXISTS "profile_json",
  DROP COLUMN IF EXISTS "bio",
  DROP COLUMN IF EXISTS "display_name";

DROP INDEX IF EXISTS "public"."idx_hasn_humans_sync_revision";
ALTER TABLE "public"."hasn_humans"
  DROP COLUMN IF EXISTS "sync_revision",
  DROP COLUMN IF EXISTS "policy_revision",
  DROP COLUMN IF EXISTS "profile_revision";

COMMIT;
