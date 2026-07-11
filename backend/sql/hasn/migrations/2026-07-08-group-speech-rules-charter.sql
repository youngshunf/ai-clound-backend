-- =====================================================
-- 群聊发言规则与分身群内准则（doc10 GS1-C1）
-- 字段变更规则：不新表、不 codegen，直改 model + 迁移 SQL。
--   1. hasn_conversations 加「是否允许普通成员拉分身进群」开关（默认允许，福仔拍板）；
--   2. hasn_group_members 加 per-(群,分身) 发言准则 charter + 更新时间（仅分身主人可读写）。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。
-- =====================================================

-- 需求 3：群主可设置是否允许其他成员拉分身进群（默认 TRUE，对齐「任意成员可拉人」现状）
ALTER TABLE "public"."hasn_conversations"
  ADD COLUMN IF NOT EXISTS "allow_member_invite_agent" BOOLEAN NOT NULL DEFAULT TRUE;
COMMENT ON COLUMN "public"."hasn_conversations"."allow_member_invite_agent"
  IS '是否允许普通成员拉分身进群 (true:允许/false:仅群主管理员)';

-- 需求 4：主人为自己的分身设置本群发言准则（仅 member_type=agent 行有意义，仅分身主人可读写，仅本群生效）
ALTER TABLE "public"."hasn_group_members"
  ADD COLUMN IF NOT EXISTS "agent_charter" TEXT NULL;
COMMENT ON COLUMN "public"."hasn_group_members"."agent_charter"
  IS '分身群内发言准则（仅 member_type=agent 行有意义，由分身主人设置，仅本群生效）';

ALTER TABLE "public"."hasn_group_members"
  ADD COLUMN IF NOT EXISTS "charter_updated_time" TIMESTAMPTZ NULL;
COMMENT ON COLUMN "public"."hasn_group_members"."charter_updated_time"
  IS '发言准则最后更新时间';
