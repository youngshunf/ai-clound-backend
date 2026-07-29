-- R2-07 · hasn_sync 内核实体化：append_event PG 函数 + 去重键列（doc92 §4 R2-07 / doc16 §3.2/§8.1）
--
-- 目标：把 sync 事件的「唯一写入口」从 Python chokepoint（SELECT MAX+1 + INSERT，advisory
-- lock 串行化）下沉为一个 PG 函数 hasn_sync.append_event(...)，让 revision 分配 + 幂等去重
-- 都在数据库内一次完成，杜绝「函数 + ORM 直写」双路径（§3.2 单实现）。
--
-- 三件事：
--   ① CREATE SCHEMA hasn_sync —— 内核函数的归属 schema（表暂仍在 public.hasn_sync_events，
--      R2-11 再统一 SET SCHEMA）。
--   ② hasn_sync_events 加两列 producer / source_event_id + 部分唯一索引 —— 跨重启幂等去重键
--      （producer='hasn_im' + 集成事件 event_id + owner_id）。sync_projector 扇出时每个 owner
--      用同一 source_event_id，故去重键必含 owner_id（否则只写第一个 owner、其余被误去重）。
--   ③ hasn_sync.append_event(...) 函数 —— 固定 search_path、producer/source_event_id 同在校验、
--      per-owner advisory xact lock 先行、幂等 return-existing、MAX+1 gapless revision、INSERT。
--
-- 幂等：可反复执行（IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE）。

-- ① 内核 schema
CREATE SCHEMA IF NOT EXISTS hasn_sync;
COMMENT ON SCHEMA hasn_sync IS 'IM 同步内核（doc16）——append_event 等入口函数归属；表 R2-11 再 SET SCHEMA 迁入';

-- ② 去重键两列（都可空：未带 producer 的历史/内部写入方不受影响）
ALTER TABLE public.hasn_sync_events
    ADD COLUMN IF NOT EXISTS producer        VARCHAR(40),
    ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(64);

COMMENT ON COLUMN public.hasn_sync_events.producer IS
    '产生该 sync 事件的上游子系统标识（如 hasn_im）；与 source_event_id 同在同缺，构成跨重启去重键';
COMMENT ON COLUMN public.hasn_sync_events.source_event_id IS
    '上游源事件 id（如集成事件 event_id）；(owner_id, producer, source_event_id) 唯一，扇出各 owner 各一行';

-- 部分唯一索引：仅约束带 producer 的行；(owner_id, producer, source_event_id) 全局唯一。
-- 这是幂等的硬后盾——即便函数内去重检查因异常竞态漏判，唯一约束也拦下重复插入。
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_sync_events_producer_source
    ON public.hasn_sync_events (owner_id, producer, source_event_id)
    WHERE producer IS NOT NULL AND source_event_id IS NOT NULL;

-- ③ 内核函数：sync 事件唯一写入口
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
-- 固定 search_path：内置函数一律经 pg_catalog 解析，杜绝 search_path 注入；业务表全限定 public.*
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_existing_revision bigint;
    v_existing_event_id text;
    v_next_revision     bigint;
    v_event_id          text;
    v_occurred_at       timestamptz;
BEGIN
    -- 所有调用方共享同一组边界：先在函数内拒绝空标识、越界字段、非法 producer 和超大载荷，
    -- 避免不同 Python producer 各自实现一套会漂移的校验。
    IF p_owner_id IS NULL OR btrim(p_owner_id) = '' OR char_length(p_owner_id) > 40 THEN
        RAISE EXCEPTION 'hasn_sync.append_event: owner_id 必须为 1 至 40 个字符'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_hasn_id IS NULL OR btrim(p_hasn_id) = '' OR char_length(p_hasn_id) > 40 THEN
        RAISE EXCEPTION 'hasn_sync.append_event: hasn_id 必须为 1 至 40 个字符'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_event_type IS NULL OR btrim(p_event_type) = '' OR char_length(p_event_type) > 50 THEN
        RAISE EXCEPTION 'hasn_sync.append_event: event_type 必须为 1 至 50 个字符'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_aggregate_type IS NULL OR btrim(p_aggregate_type) = '' OR char_length(p_aggregate_type) > 40 THEN
        RAISE EXCEPTION 'hasn_sync.append_event: aggregate_type 必须为 1 至 40 个字符'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_aggregate_id IS NULL OR btrim(p_aggregate_id) = '' OR char_length(p_aggregate_id) > 80 THEN
        RAISE EXCEPTION 'hasn_sync.append_event: aggregate_id 必须为 1 至 80 个字符'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'hasn_sync.append_event: payload 必须是 JSON object'
            USING ERRCODE = 'check_violation';
    END IF;
    IF octet_length(p_payload::text) > 262144 THEN
        RAISE EXCEPTION 'hasn_sync.append_event: payload 不能超过 262144 字节'
            USING ERRCODE = 'check_violation';
    END IF;

    -- producer / source_event_id 是去重键的两半，必须同时提供或同时省略（缺一则去重语义残缺）
    IF (p_producer IS NULL) <> (p_source_event_id IS NULL) THEN
        RAISE EXCEPTION
            'hasn_sync.append_event: producer 与 source_event_id 必须同时提供或同时省略 (producer=%, source_event_id=%)',
            p_producer, p_source_event_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_producer IS NOT NULL AND (
        char_length(p_producer) > 40
        OR p_producer !~ '^[a-z][a-z0-9_]{0,39}$'
    ) THEN
        RAISE EXCEPTION
            'hasn_sync.append_event: producer 必须匹配 ^[a-z][a-z0-9_]{0,39}$'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_source_event_id IS NOT NULL AND (
        btrim(p_source_event_id) = ''
        OR char_length(p_source_event_id) > 64
    ) THEN
        RAISE EXCEPTION 'hasn_sync.append_event: source_event_id 必须为 1 至 64 个字符'
            USING ERRCODE = 'check_violation';
    END IF;

    -- ⓐ 先拿 per-owner 事务级 advisory lock：串行化同一 owner 的 revision 分配 + 去重检查。
    --    后到事务阻塞到前一个提交后再读 MAX/查重 —— 既拿到正确的下一个 revision（无空洞/冲突），
    --    也看得见前一个已提交的去重行。锁在事务结束（commit/rollback）自动释放；hashtext 分桶
    --    极少量跨 owner 串行无害。这是现网 _append_sync_event_with_id 同款串行化策略的下沉。
    PERFORM pg_advisory_xact_lock(hashtext(p_owner_id));

    -- ⓑ 幂等：带 producer 时，命中 (owner_id, producer, source_event_id) 已落行即返回原 revision，
    --    deduped=true，不再插新行。跨重启重放（消费者 cursor 之外的第二层兜底）在此收敛。
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

    -- ⓒ 分配下一个 gapless revision（锁内读 MAX，无并发空洞）
    SELECT coalesce(max(e.revision), 0) + 1
      INTO v_next_revision
      FROM public.hasn_sync_events e
     WHERE e.owner_id = p_owner_id;

    -- 事件 id 沿用现网 'se_' + 24 hex 形状（varchar(40) 容得下），换 gen_random_uuid 在库内生成
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

COMMENT ON FUNCTION hasn_sync.append_event(text, text, text, text, text, jsonb, text, text, timestamptz) IS
    'IM 同步事件唯一写入口（doc16 §3.2）：per-owner advisory lock 串行化 gapless revision 分配 + (owner,producer,source_event_id) 幂等去重；返回 (revision, event_id, deduped)';
