-- =====================================================
-- 群聊披露档（doc08 §3.4 RT2.5 · D9-D11）
-- 字段变更规则：不新表、不 codegen，直改 model + 迁移 SQL。
--   hasn_group_members 加 per-(群,分身) 披露档：主人为自己入群分身按群设的披露档位。
--   语义复用 social 信任等级 2/3/4（普通朋友/好友/密友），默认 2；仅分身主人可读写。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。
-- =====================================================

-- D9：主人为自己入群分身设的披露档（仅 member_type=agent 行有业务意义；human 行保持默认值不消费）
ALTER TABLE "public"."hasn_group_members"
  ADD COLUMN IF NOT EXISTS "agent_group_trust_level" SMALLINT NOT NULL DEFAULT 2;
COMMENT ON COLUMN "public"."hasn_group_members"."agent_group_trust_level"
  IS '分身群内披露档 (2:普通朋友:blue/3:好友:green/4:密友:purple)·仅agent行有意义·仅分身主人可读写·doc08 §3.4';
