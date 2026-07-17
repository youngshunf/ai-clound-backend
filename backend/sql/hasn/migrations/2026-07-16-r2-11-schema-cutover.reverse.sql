-- R2-11 · 维护窗口 schema 硬切换（反向）· doc16 §10.4 回滚边界
--
-- §10.4：**开放写入前**可执行反向 SET SCHEMA/migration 或恢复快照，再启动旧版本；回滚脚本必须
--   **同时恢复 schema、role、sequence/default、函数和应用版本**，不能只移动表。
--
-- 本脚本做**结构级**反向：把表移回 public + 恢复 hasn_im_ 前缀、append_event 换回 public 源、
-- 撤销 grant、DROP 角色、drop hasn_im schema、drop R2-11 新增结构（agent_communication_settings /
-- 异常清单表 / history_complete_from_seq 列）、恢复漂移默认值。
--
-- ⚠️ 数据级回滚（§10.4）：R2-11 §10.2 回填的成员 epoch 行是**前向幂等**产物，本反向脚本**不删**这些
--   数据行——真正的数据反迁移 = 恢复快照（这正是 R2-11 只在快照副本演练的原因）。反向后再跑前向，
--   NOT EXISTS 守卫保证不重复插入。
--
-- 幂等：全部带存在性守卫，可重复执行。

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- ① append_event 换回 public 源（sync 表即将移回 public）
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION hasn_sync.append_event(
    p_owner_id        text,
    p_hasn_id         text,
    p_event_type      text,
    p_aggregate_type  text,
    p_aggregate_id    text,
    p_payload         jsonb,
    p_producer        text        DEFAULT NULL,
    p_source_event_id text        DEFAULT NULL,
    p_occurred_at     timestamptz DEFAULT NULL
)
RETURNS TABLE(revision bigint, event_id text, deduped boolean)
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_existing_revision bigint;
    v_existing_event_id text;
    v_next_revision     bigint;
    v_event_id          text;
    v_occurred_at       timestamptz;
BEGIN
    IF (p_producer IS NULL) <> (p_source_event_id IS NULL) THEN
        RAISE EXCEPTION
            'hasn_sync.append_event: producer 与 source_event_id 必须同时提供或同时省略 (producer=%, source_event_id=%)',
            p_producer, p_source_event_id
            USING ERRCODE = 'check_violation';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext(p_owner_id));

    IF p_producer IS NOT NULL THEN
        SELECT e.revision, e.event_id
          INTO v_existing_revision, v_existing_event_id
          FROM public.hasn_sync_events e
         WHERE e.owner_id = p_owner_id
           AND e.producer = p_producer
           AND e.source_event_id = p_source_event_id
         LIMIT 1;
        IF FOUND THEN
            revision := v_existing_revision;
            event_id := v_existing_event_id;
            deduped := true;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;

    SELECT coalesce(max(e.revision), 0) + 1
      INTO v_next_revision
      FROM public.hasn_sync_events e
     WHERE e.owner_id = p_owner_id;

    v_event_id := 'se_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 24);
    v_occurred_at := coalesce(p_occurred_at, now());

    INSERT INTO public.hasn_sync_events (
        event_id, owner_id, hasn_id, event_type, aggregate_type, aggregate_id,
        payload, revision, producer, source_event_id, occurred_at, created_time, updated_time
    ) VALUES (
        v_event_id, p_owner_id, p_hasn_id, p_event_type, p_aggregate_type, p_aggregate_id,
        p_payload, v_next_revision, p_producer, p_source_event_id, v_occurred_at, now(), now()
    );

    revision := v_next_revision;
    event_id := v_event_id;
    deduped := false;
    RETURN NEXT;
    RETURN;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════
-- ② 移表回 public（sync → public 保留名；事件表恢复 hasn_im_ 前缀；IM 归属表回 public）
-- ═══════════════════════════════════════════════════════════════════════════

-- 2a. sync 表回 public
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'hasn_sync' AND tablename = 'hasn_sync_events') THEN
        ALTER TABLE hasn_sync.hasn_sync_events SET SCHEMA public;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'hasn_sync' AND tablename = 'hasn_sync_inbox_events') THEN
        ALTER TABLE hasn_sync.hasn_sync_inbox_events SET SCHEMA public;
    END IF;
END $$;

-- 2b. 事件/消费者表恢复 hasn_im_ 前缀并回 public
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'hasn_im' AND tablename = 'integration_events') THEN
        ALTER TABLE hasn_im.integration_events RENAME TO hasn_im_integration_events;
        ALTER TABLE hasn_im.hasn_im_integration_events SET SCHEMA public;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'hasn_im' AND tablename = 'event_consumer_offsets') THEN
        ALTER TABLE hasn_im.event_consumer_offsets RENAME TO hasn_im_event_consumer_offsets;
        ALTER TABLE hasn_im.hasn_im_event_consumer_offsets SET SCHEMA public;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'hasn_im' AND tablename = 'event_consumer_failures') THEN
        ALTER TABLE hasn_im.event_consumer_failures RENAME TO hasn_im_event_consumer_failures;
        ALTER TABLE hasn_im.hasn_im_event_consumer_failures SET SCHEMA public;
    END IF;
END $$;

-- 2c. IM 归属表回 public（保留名）
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'hasn_messages', 'hasn_conversations', 'hasn_conversation_memberships',
        'hasn_unread_projection', 'hasn_group_agent_invites', 'hasn_suppressed_messages',
        'hasn_asset_grants', 'hasn_contacts', 'hasn_contact_requests'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'hasn_im' AND tablename = t) THEN
            EXECUTE format('ALTER TABLE hasn_im.%I SET SCHEMA public', t);
        END IF;
    END LOOP;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- ③ drop R2-11 新增结构（回填数据行按 §10.4 由快照恢复，不在此删）
-- ═══════════════════════════════════════════════════════════════════════════

-- history_complete_from_seq 列（表已移回 public）
ALTER TABLE public.hasn_conversation_memberships DROP COLUMN IF EXISTS history_complete_from_seq;

-- R2-11 新建的 hasn_im 表（异常清单 + agent 通信设置投影）
DROP TABLE IF EXISTS hasn_im.membership_read_backfill_exceptions;
DROP TABLE IF EXISTS hasn_im.agent_communication_settings;

-- ═══════════════════════════════════════════════════════════════════════════
-- ④ 撤销 grant + DROP 角色 + drop hasn_im schema
--    （hasn_sync schema/append_event 由 R2-07 拥有，保留——本反向只把函数换回 public 源）
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['astra_im_service', 'astra_sync_service', 'astra_python_backend']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            -- DROP OWNED BY 清除该角色被授予的一切权限（及其拥有对象），使 DROP ROLE 无依赖
            EXECUTE format('DROP OWNED BY %I', r);
            EXECUTE format('DROP ROLE %I', r);
        END IF;
    END LOOP;
END $$;

-- hasn_im schema 此时应为空（表均已移回 public、新建表已 drop）
DROP SCHEMA IF EXISTS hasn_im RESTRICT;

-- ═══════════════════════════════════════════════════════════════════════════
-- ⑤ 恢复字段漂移默认值（对齐 R2-11 前状态）——dev 库前状即 false/1，此步为对称兜底
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.hasn_messages ALTER COLUMN mention_all SET DEFAULT false;
ALTER TABLE public.hasn_contacts ALTER COLUMN trust_level SET DEFAULT 1;

COMMIT;
