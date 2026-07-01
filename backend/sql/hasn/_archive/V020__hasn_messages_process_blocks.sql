-- =====================================================
-- HASN messages process_blocks migration
-- Adds ordered message-generation/process events beside canonical content.
-- =====================================================

ALTER TABLE "public"."hasn_messages"
  ADD COLUMN IF NOT EXISTS "process_blocks" jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN "public"."hasn_messages"."process_blocks"
  IS '消息生成过程块（JSONB 数组，按产生顺序保存 stream_chunk/tool_call/status 等事件）';
