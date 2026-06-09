-- =====================================================
-- 会话唯一性收敛：hasn_conversations 一对一/一群只允许一行
--   direct: 一对参与者（排序后 a<b）只允许一个 direct 会话，**与 relation_type 无关**
--   group : 一个 group_id 只允许一个 group 会话
-- 背景：历史上 get_or_create_conversation 把 relation_type 纳入查找键 + 查改非原子
--   + 无 DB 唯一约束，导致同一对参与者沉淀出多行（换设备 sync_pull 全量回放后一次性
--   显形为「列表里好几个同一个人」）。本迁移：① 清洗存量重复（repoint 引用表→canonical
--   →删重）② 建 partial unique index 兜底。配套代码改 get_or_create 走 advisory lock +
--   仅按参与者对去重（见 message_router.get_or_create_conversation）。
-- canonical 选取口径：每组最早 created_time（tie-break id），与运行期 get_or_create 一致。
-- 幂等：每条语句自包含（inline CTE，无 TEMP 表），可重复执行；去重后再跑全是 no-op。
-- =====================================================

-- ========== 1. DIRECT 去重：把引用重指到 canonical，删除非 canonical 行 ==========

-- 1.1 hasn_messages（UUID conversation_id）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_messages" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 1.2 hasn_sync_events（UUID，可空）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_sync_events" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 1.3 hasn_asset_grants（UUID）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_asset_grants" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 1.4 hasn_suppressed_messages（UUID）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_suppressed_messages" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 1.5 hasn_unread_counts（UUID）。repoint 可能产生同 (hasn_id, canon) 多行；未读计数为可
--     重算缓存、无唯一约束，留之无害，不在此合并。
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_unread_counts" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 1.6 hasn_sessions（String conversation_id，需 cast canon_id::text）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_sessions" t SET conversation_id = dup.canon_id::text
FROM dup WHERE t.conversation_id = dup.dup_id::text AND dup.dup_id <> dup.canon_id;

-- 1.7 hasn_task_run（String source_conversation_id）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
UPDATE "public"."hasn_task_run" t SET source_conversation_id = dup.canon_id::text
FROM dup WHERE t.source_conversation_id = dup.dup_id::text AND dup.dup_id <> dup.canon_id;

-- 1.8 删除非 canonical 的 direct 重复行
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY participant_a_id, participant_b_id
               ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'direct'
)
DELETE FROM "public"."hasn_conversations" c
USING dup WHERE c.id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- ========== 2. GROUP 去重（按 group_id）==========

-- 2.1 hasn_messages
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_messages" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.2 hasn_sync_events
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_sync_events" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.3 hasn_asset_grants
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_asset_grants" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.4 hasn_suppressed_messages
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_suppressed_messages" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.5 hasn_unread_counts
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_unread_counts" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.6 hasn_group_members
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_group_members" t SET conversation_id = dup.canon_id
FROM dup WHERE t.conversation_id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.7 hasn_sessions / hasn_task_run（String）
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_sessions" t SET conversation_id = dup.canon_id::text
FROM dup WHERE t.conversation_id = dup.dup_id::text AND dup.dup_id <> dup.canon_id;

WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
UPDATE "public"."hasn_task_run" t SET source_conversation_id = dup.canon_id::text
FROM dup WHERE t.source_conversation_id = dup.dup_id::text AND dup.dup_id <> dup.canon_id;

-- 2.8 删除非 canonical 的 group 重复行
WITH dup AS (
    SELECT id AS dup_id,
           first_value(id) OVER (
               PARTITION BY group_id ORDER BY created_time ASC, id ASC
           ) AS canon_id
    FROM "public"."hasn_conversations"
    WHERE type = 'group' AND group_id IS NOT NULL
)
DELETE FROM "public"."hasn_conversations" c
USING dup WHERE c.id = dup.dup_id AND dup.dup_id <> dup.canon_id;

-- 2.9 group_members 去重：repoint 后同一 canonical 可能出现 (conversation_id, member_id)
--     重复，按 ctid 保留一行删其余。
DELETE FROM "public"."hasn_group_members" gm
USING (
    SELECT ctid,
           row_number() OVER (
               PARTITION BY conversation_id, member_id ORDER BY ctid
           ) AS rn
    FROM "public"."hasn_group_members"
) x
WHERE gm.ctid = x.ctid AND x.rn > 1;

-- ========== 3. partial unique index 兜底 ==========
CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_conversations_direct"
    ON "public"."hasn_conversations" (participant_a_id, participant_b_id)
    WHERE type = 'direct';

CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_conversations_group"
    ON "public"."hasn_conversations" (group_id)
    WHERE type = 'group' AND group_id IS NOT NULL;
