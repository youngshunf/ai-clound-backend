-- 分身发起会话因果闭环 doc14 S1（§6.2 B 刀 + §6.5 E 刀）：云端溯源与差事简报两列。
-- 背景：分身在发起方会话（主会话/工作会话）里发起的对外聊天，发起方会话拿不到结果。
--   本期补「消息级发起溯源」——记录**哪个发起方 runtime 会话产生了这条消息**，daemon 据此
--   在对端出结果时把结果回灌回发起方会话（工作会话续派 / 主会话冒泡）。
--   ① hasn_messages.origin_session_id —— 对齐 doc02 origin_node_id 的既有范式：Server 侧从
--      AgentContext 会话 id（_hasn_session_id 剥离产物）自动填、**不收客户端入参、不可伪造**；
--      发起方私有——message.new 事件仅发送方 owner 携带，对端 owner 剥除（§7-1）。
--   ② hasn_conversations.mission_note —— 差事背景（分身自撰一句话），仅**发起层建会话时**写入；
--      发送方 owner 私有框定，会话对象投影只对 mission_note_owner_id 序列化，对端 owner 裁剪。
--   ③ hasn_conversations.mission_note_owner_id —— mission_note 的归属 owner（投影裁剪判据，
--      零推断：不从首条消息反推「发送方」，显式落列）。
-- 幂等：ADD COLUMN IF NOT EXISTS，runner 只跑一次、重复跑也安全。

-- ① 消息级发起溯源：产生该消息的发起方 runtime 会话（工作会话 id 或主会话 runtime session id）。
ALTER TABLE hasn_messages ADD COLUMN IF NOT EXISTS origin_session_id VARCHAR(64);
COMMENT ON COLUMN hasn_messages.origin_session_id IS '产生该消息的发起方 runtime 会话 id（工作会话/主会话·Server 侧从 AgentContext 自动填·不可伪造·仅发送方 owner 的事件携带·doc14 §6.2）';

-- ② 差事背景（分身自撰一句话，发起层建会话时写入；发送方 owner 私有，对端投影裁剪）。
ALTER TABLE hasn_conversations ADD COLUMN IF NOT EXISTS mission_note TEXT;
COMMENT ON COLUMN hasn_conversations.mission_note IS '差事背景（分身自撰一句话·发起层建会话时写入·发送方 owner 私有·peer 会话首派随 IdentityPeer 注入·doc14 §6.5）';

-- ③ mission_note 归属 owner（投影裁剪判据）。
ALTER TABLE hasn_conversations ADD COLUMN IF NOT EXISTS mission_note_owner_id VARCHAR(40);
COMMENT ON COLUMN hasn_conversations.mission_note_owner_id IS 'mission_note 的归属 owner hasn_id（会话对象投影只对该 owner 序列化 mission_note·对端 owner 裁剪·doc14 §6.5）';
