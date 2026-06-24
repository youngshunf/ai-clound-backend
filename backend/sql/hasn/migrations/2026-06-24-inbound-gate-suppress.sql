-- 入站消息门控与抑制箱（外部→Agent 全门控）数据层迁移
-- 事实源：docs/hasn-node设计文档/05-安全与权限/06-入站消息门控与抑制箱(外部→Agent全门控).md
-- 实施清单 S1：
--   ① hasn_agents 新增 inbound_policy 列——外部入站消息门控策略（manual_only 门控的数据源）。
--   ② hasn_suppressed_messages.suppress_reason 字典扩 5 个门控理由（列本身是 varchar(40) 无 CHECK，
--      只需更新 COMMENT 让管理端字典与 webui 类目对齐；扩值域零 DDL，存量行不受影响）。
-- 幂等：ADD COLUMN IF NOT EXISTS + COMMENT ON 可重跑。
-- 说明：D1 的 owner 级 show_blocked_in_inbox 开关按 YAGNI 留 P2——本期 trust=0/blocked 默认静默拒绝（不进抑制箱），
--   无需开关字段即得默认行为。

-- ① hasn_agents 入站门控策略（auto 默认放行 / manual_strangers 陌生人需放行 / manual_all 全部需放行）
ALTER TABLE hasn_agents
    ADD COLUMN IF NOT EXISTS inbound_policy varchar(20) NOT NULL DEFAULT 'auto';

COMMENT ON COLUMN hasn_agents.inbound_policy IS
    '外部入站消息门控策略 (auto:自动放行:green/manual_strangers:陌生人需放行:orange/manual_all:全部需放行:red)，默认自动，主人可设手动审阅';

-- ② 抑制理由字典扩 5 门控值（permission_denied/social_disabled/agent_frozen/abuse_restricted/manual_only）
COMMENT ON COLUMN hasn_suppressed_messages.suppress_reason IS
    '抑制原因 (runtime_unavailable:Runtime不可用:orange/adapter_missing:Adapter缺失:red/handle_unavailable:Handle不可用:orange/owner_confirmation_required:需Owner确认:purple/policy_suppressed:策略抑制:gray/social_disabled:未开启社交:gray/permission_denied:权限不足:red/agent_frozen:分身已冻结:orange/abuse_restricted:风控限制:red/manual_only:待手动放行:purple)';
