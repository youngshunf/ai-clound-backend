-- =====================================================
-- HASN 群内拉分身邀请确认表（doc10 GS1-C6 · §3.2）
-- 「拉别人的分身进群需其主人同意」：非主人（含群主/管理员）发起拉分身 → 不直接入群，
-- 落一条 pending 邀请给分身主人确认，主人 accept 后才插 hasn_group_members。
-- 分身主人本人拉自己的分身即时入群、不走本表。
-- 独立邀请表干净隔离——绝不给 hasn_group_members 加 status 列污染成员语义。
-- =====================================================
CREATE TABLE "public"."hasn_group_agent_invites" (
  "id"                bigserial PRIMARY KEY,
  "conversation_id"   uuid NOT NULL,
  "group_id"          varchar(20) NOT NULL,
  "agent_hasn_id"     varchar(40) NOT NULL,
  "agent_owner_id"    varchar(40) NOT NULL,
  "inviter_id"        varchar(40) NOT NULL,
  "status"            varchar(16) NOT NULL DEFAULT 'pending',
  "created_time"      timestamptz(6) NOT NULL DEFAULT now(),
  "resolved_time"     timestamptz(6),
  "updated_time"      timestamptz(6)
);

CREATE INDEX "idx_hasn_group_agent_invites_owner" ON "public"."hasn_group_agent_invites" ("agent_owner_id", "status");
CREATE INDEX "idx_hasn_group_agent_invites_conv" ON "public"."hasn_group_agent_invites" ("conversation_id");
-- 同一 (群, 分身) 仅允许一条 pending 邀请（幂等：已在群/已 pending 拒绝重复发起）
CREATE UNIQUE INDEX "uq_hasn_group_agent_invites_pending"
  ON "public"."hasn_group_agent_invites" ("conversation_id", "agent_hasn_id")
  WHERE "status" = 'pending';

COMMENT ON TABLE "public"."hasn_group_agent_invites" IS 'HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."conversation_id" IS '群会话 ID（关联 hasn_conversations）';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."group_id" IS '群组公开标识（g:NNNNNN）';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."agent_hasn_id" IS '被邀请的分身 hasn_id';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."agent_owner_id" IS '分身主人 hasn_id（冗余列，便于按主人查询/判权）';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."inviter_id" IS '发起人 hasn_id';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."status" IS '状态 (pending:待确认:orange/accepted:已同意:green/declined:已拒绝:red/expired:已过期:gray/cancelled:已取消:gray)';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."resolved_time" IS '处理时间（accept/decline/expire/cancel）';
COMMENT ON COLUMN "public"."hasn_group_agent_invites"."updated_time" IS '更新时间';
