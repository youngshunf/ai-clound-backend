-- R2-02 · 会话内消息序号模型（doc16 §4.1 / doc92 R2-02）
--
-- 权威顺序事实 = 每会话单调递增的 conversation_seq（禁时间戳 / MAX(seq)+1 / 客户端序号）。
-- 分配方式：同事务内 `UPDATE hasn_conversations SET current_seq = current_seq + 1 ... RETURNING`，
-- 行锁串行化同会话并发发送。本迁移建列 + 回填历史 + 收紧 NOT NULL + 唯一约束。
--
-- 幂等：全部 IF NOT EXISTS / 条件回填，可重复执行。
-- 归属：R2 期表仍在 hasn schema（默认 search_path），R2-11 维护窗口 SET SCHEMA 迁 hasn_im。

BEGIN;

-- 1) 会话级下一序号游标（默认 0，回填后重算）
ALTER TABLE hasn_conversations ADD COLUMN IF NOT EXISTS current_seq BIGINT NOT NULL DEFAULT 0;
COMMENT ON COLUMN hasn_conversations.current_seq IS '会话内消息序号游标（下一条 = current_seq+1·UPDATE RETURNING 原子分配·§4.1）';

-- 2) 消息级会话内序号（先建为可空，回填后收紧）
ALTER TABLE hasn_messages ADD COLUMN IF NOT EXISTS conversation_seq BIGINT;
COMMENT ON COLUMN hasn_messages.conversation_seq IS '本消息在所属会话内的单调序号（权威顺序事实·唯一·§4.1）';

-- 3) 回填历史消息 conversation_seq：按全局 id 升序（= 落库时序）在每会话内从 1 递增
WITH numbered AS (
    SELECT id, row_number() OVER (PARTITION BY conversation_id ORDER BY id) AS rn
    FROM hasn_messages
)
UPDATE hasn_messages m
SET conversation_seq = numbered.rn
FROM numbered
WHERE m.id = numbered.id
  AND m.conversation_seq IS NULL;

-- 4) 回填会话 current_seq = 该会话已回填的最大 conversation_seq（无消息则 0）
UPDATE hasn_conversations c
SET current_seq = COALESCE(
    (SELECT MAX(m.conversation_seq) FROM hasn_messages m WHERE m.conversation_id = c.id),
    0
);

-- 5) 收紧：conversation_seq 非空 + 同会话唯一（并发无重复/倒退 seq 的落库硬约束）
ALTER TABLE hasn_messages ALTER COLUMN conversation_seq SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_messages_conversation_seq
    ON hasn_messages (conversation_id, conversation_seq);

COMMIT;
