-- R2-03b 会话成员周期的展示兼容字段。
--
-- 这是 R2-11 维护窗口前可在线执行的 additive migration，使前切换代码能够读写统一
-- membership 模型。R2-11 仍重复使用 IF NOT EXISTS 补齐这些列，保证维护窗口脚本自包含。

BEGIN;

ALTER TABLE public.hasn_conversation_memberships
    ADD COLUMN IF NOT EXISTS member_star_id VARCHAR(40) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS member_name VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS muted BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS invited_by VARCHAR(40) NULL,
    ADD COLUMN IF NOT EXISTS charter_updated_time TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS history_complete_from_seq BIGINT NULL;

COMMENT ON COLUMN public.hasn_conversation_memberships.member_star_id IS
    '成员唤星号展示快照；身份权威仍是 member_hasn_id';
COMMENT ON COLUMN public.hasn_conversation_memberships.member_name IS
    '成员名称展示快照；身份权威仍是 member_hasn_id';
COMMENT ON COLUMN public.hasn_conversation_memberships.muted IS
    '成员是否免打扰';
COMMENT ON COLUMN public.hasn_conversation_memberships.invited_by IS
    '邀请者 hasn_id';
COMMENT ON COLUMN public.hasn_conversation_memberships.charter_updated_time IS
    '分身群内发言准则最后更新时间';
COMMENT ON COLUMN public.hasn_conversation_memberships.history_complete_from_seq IS
    '已证明本地历史完整的最早 message seq；为空表示未知';

COMMIT;
