-- R2-03 成员周期模型（doc16 §4.2/§4.3·D7/D8）
-- 本地 schema 基线：表建在 public，R2-11 再 SET SCHEMA 移入 hasn_im。幂等可重复执行。
--
-- 权威 = seq + 可见区间 + read_seq，不退回计数事实。多周期 epoch：一行=一次加入周期，
-- 退出闭合不删行，重入建新行；部分唯一索引限一个活动周期；direct 双方永久 epoch。

-- 1) 成员周期表：多周期 epoch
CREATE TABLE IF NOT EXISTS hasn_conversation_memberships (
    id                        BIGSERIAL PRIMARY KEY,
    conversation_id           UUID        NOT NULL,
    member_hasn_id            VARCHAR(40) NOT NULL,
    member_type               VARCHAR(10) NOT NULL DEFAULT 'human',
    role                      VARCHAR(20) NOT NULL DEFAULT 'member',
    joined_seq                BIGINT      NOT NULL,
    left_seq                  BIGINT      NULL,
    read_seq                  BIGINT      NOT NULL DEFAULT 0,
    state                     VARCHAR(10) NOT NULL DEFAULT 'active',
    -- 群策略字段（仅群会话成员有意义，语义复用 hasn_group_members）
    agent_group_trust_level   SMALLINT    NOT NULL DEFAULT 2,
    agent_charter             TEXT        NULL,
    joined_at                 TIMESTAMPTZ NULL,
    left_at                   TIMESTAMPTZ NULL,
    created_time              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_time              TIMESTAMPTZ NULL
);

COMMENT ON TABLE  hasn_conversation_memberships                     IS 'HASN 会话成员周期表（多周期 epoch·doc16 §4.2）';
COMMENT ON COLUMN hasn_conversation_memberships.conversation_id     IS '所属会话 ID';
COMMENT ON COLUMN hasn_conversation_memberships.member_hasn_id      IS '成员 hasn_id';
COMMENT ON COLUMN hasn_conversation_memberships.member_type         IS '成员类型 (human:人类/agent:代理/service:系统)';
COMMENT ON COLUMN hasn_conversation_memberships.role                IS '角色 (owner:群主/admin:管理员/member:成员)';
COMMENT ON COLUMN hasn_conversation_memberships.joined_seq          IS '本周期加入时的会话序号下界（可见 message.seq >= joined_seq）';
COMMENT ON COLUMN hasn_conversation_memberships.left_seq            IS '本周期退出时的会话序号上界（NULL=活动周期·可见 message.seq <= left_seq）';
COMMENT ON COLUMN hasn_conversation_memberships.read_seq            IS '本周期已读游标（单调只进·clamp 到可见上界·§4.3）';
COMMENT ON COLUMN hasn_conversation_memberships.state               IS '状态 (active:活动/left:主动退出/removed:被移除/banned:被封)';
COMMENT ON COLUMN hasn_conversation_memberships.agent_group_trust_level IS '分身群内披露档 (2:普通朋友/3:好友/4:密友)·doc08 §3.4';
COMMENT ON COLUMN hasn_conversation_memberships.agent_charter       IS '分身群内发言准则（仅分身主人可读写）';

-- 2) 部分唯一索引：同一 (会话, 成员) 最多一个活动周期（left_seq IS NULL）
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_membership_active_epoch
    ON hasn_conversation_memberships (conversation_id, member_hasn_id)
    WHERE left_seq IS NULL;

-- 3) 可见区间查询索引（按会话 + 成员取周期）
CREATE INDEX IF NOT EXISTS idx_hasn_membership_conv_member
    ON hasn_conversation_memberships (conversation_id, member_hasn_id);

-- 4) 可重建未读投影（read model·§4.3）——明确标注可重建，权威永远是 message/membership/read_seq
CREATE TABLE IF NOT EXISTS hasn_unread_projection (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     UUID        NOT NULL,
    member_hasn_id      VARCHAR(40) NOT NULL,
    unread_count        INTEGER     NOT NULL DEFAULT 0,
    computed_at_seq     BIGINT      NOT NULL DEFAULT 0,
    created_time        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_time        TIMESTAMPTZ NULL
);

COMMENT ON TABLE  hasn_unread_projection                  IS 'HASN 未读投影（可重建 read model·doc16 §4.3·非权威·reconciler 按序号重算）';
COMMENT ON COLUMN hasn_unread_projection.unread_count     IS '未读数（可重建·漂移时以 message/membership/read_seq 为准）';
COMMENT ON COLUMN hasn_unread_projection.computed_at_seq  IS '本投影计算时的会话 current_seq（判是否需要重算）';

CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_unread_projection_conv_member
    ON hasn_unread_projection (conversation_id, member_hasn_id);
