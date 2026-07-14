-- 会话一等实体重构 C1（doc02 §3.1/§3.2/§3.8）：云端契约的两处 schema 补充。
-- 背景：会话本就是云端权威实体（hasn_conversations UUID 主键 + participant_a/b + group_* + 名册），
--   本重构让 daemon「按 conversation_id 拉会话对象」并把投递/同步事件瘦身为 message.new。
--   云端权威结构基本不动，只补两列：
--   ① hasn_conversations.revision —— 会话元数据版本号，成员/群名/策略变更时 +1，供 daemon
--      判断本地镜像是否过期（配合 conversation.updated 事件）。
--   ② hasn_messages.origin_node_id —— 消息级设备 meta（福仔裁决 2026-07-14「每条都带，只在需要时显示」），
--      记录产生该消息的节点；Server 侧从认证上下文自动填、不收客户端入参、不可伪造；云端 runtime 产生的
--      消息填 'cloud' 哨兵；渲染边界 join hasn_nodes.node_name 解析设备名。
-- 幂等：ADD COLUMN IF NOT EXISTS，runner 只跑一次、重复跑也安全。

-- ① 会话元数据版本号（revision）。存量行默认 1；后续变更由 bump_conversation_revision +1。
ALTER TABLE hasn_conversations ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1;
COMMENT ON COLUMN hasn_conversations.revision IS '会话元数据版本号（成员/群名/策略变更 +1，供 daemon 判镜像是否过期·doc02 §3.2）';

-- ② 消息级设备 meta：产生该消息的节点 ID（nullable，Server 侧自动填不可伪造，cloud runtime = 'cloud'）。
ALTER TABLE hasn_messages ADD COLUMN IF NOT EXISTS origin_node_id VARCHAR(64);
COMMENT ON COLUMN hasn_messages.origin_node_id IS '产生该消息的节点 ID（Server 侧从认证上下文自动填·不可伪造·cloud runtime=cloud哨兵·渲染时 join hasn_nodes 解析设备名·doc02 §3.8）';
