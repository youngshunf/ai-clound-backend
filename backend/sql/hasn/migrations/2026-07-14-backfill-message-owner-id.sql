-- doc18 P0：回填 hasn_messages.owner_id（透明视图 / hasn.message.search 归属键）
--
-- 背景（doc18 §2.6 / §5.0.3）：route_message → persist_message 历史从不回填 owner_id，导致约
--   75% 的消息行 owner_id 为空。owner 透明视图与 hasn.message.search 硬过滤 `WHERE owner_id`，
--   这些 route 落库的消息（A2A/A2H/分身回复/入站抑制）对收件方分身**不可检索** → doc18 L3
--   「聊天记录兜底」失效（「翻了聊天记录也没找到对方说过的事」的根因）。
--
-- 口径（与写路径 persist_message 一致）：1:1 消息 owner_id = **收件方 owner**
--   - 发给分身（to_id 以 a_ 开头）→ 该分身的 owner_id（join hasn_agents）
--   - 发给人（to_id 以 h_ 开头）→ 其本人（owner_id = to_id）
--   - 群消息（to_id 以 g: 开头）→ **不回填**（按 conversation_id + 成员资格归属，
--     list_group_messages 不看 owner_id；单一 owner_id 表达不了群语义）
--   发送方可见性另由 message.sent sync_event / owner_copy 旁观补投覆盖（A2AFIRST），不靠本列。
--
-- 幂等：仅回填 owner_id 为空的行，可重复执行；不动已有 owner_id 的行（hub-sync 上行行）。

-- 1) 发给分身：从 hasn_agents 取收件分身的 owner
UPDATE public.hasn_messages m
SET owner_id = a.owner_id
FROM public.hasn_agents a
WHERE (m.owner_id IS NULL OR m.owner_id = '')
  AND left(m.to_id, 2) = 'a_'
  AND a.hasn_id = m.to_id
  AND a.owner_id IS NOT NULL
  AND a.owner_id <> '';

-- 2) 发给人：收件方本人即 owner
UPDATE public.hasn_messages m
SET owner_id = m.to_id
WHERE (m.owner_id IS NULL OR m.owner_id = '')
  AND left(m.to_id, 2) = 'h_';
