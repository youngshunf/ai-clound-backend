-- R2-11 · 维护窗口 schema 硬切换（正向）· doc92 R2-11 / doc16 §3.1/§3.2/§10.2/§10.3
--
-- 本迁移是 R2 期「cutover release」的收口物产：把散落在 public 的 IM/sync/关系表统一移入
-- 独立 schema（hasn_im / hasn_sync），建 DB 角色与受限 grant，收敛 R0-01 字段漂移，完成
-- §10.2 全量回填（成员周期 epoch / read_seq 映射+异常清单 / agent 通信设置 / 消费者 cursor
-- 初始化为切换时刻 event head，存量不重投影）。
--
-- ⚠️ 归属与验收（doc92 v2.3·福仔「本地重构+测试全绿最后才生产部署」）：本脚本**只**在**本地快照
--   副本**（pg_dump 本地库 → 恢复到临时本地库）上演练——正反两向各跑通一次即达 R2-11 exit gate；
--   **不**永久应用到 dev 主库（那属 R3 维护窗口硬切换）。故本脚本不改任何在跑应用代码/常量。
--
-- 顺序对应 §10.3 步骤 4-8：① schema+role+受限函数 → ② 字段漂移收敛 → ③ 成员/read 回填
--   → ④ 移表（IM→hasn_im / sync→hasn_sync / 事件表去前缀）→ ⑤ append_event 换源 →
--   ⑥ agent 通信设置回填 → ⑦ 消费者 cursor 初始化 → ⑧ 最终 grants。
--
-- 幂等：schema/role/列/表族均带存在性守卫，可重复执行（移表用 pg_tables 判定当前 schema）。
-- 反向：见同目录 2026-07-16-r2-11-schema-cutover.reverse.sql（§10.4：同时恢复 schema/role/
--   sequence/default/函数；数据级回滚 = 快照恢复，不在反向脚本内删回填行）。

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- ① schema + DB 角色（§3.2）
-- ═══════════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS hasn_im;
COMMENT ON SCHEMA hasn_im IS 'HASN IM 独立模块 schema（消息/会话/成员/抑制/资产授权/关系/集成事件/消费者·doc16 §3.1）';

-- hasn_sync 已由 R2-07 建；此处幂等确保存在（本迁移把 sync 表迁入其中）。
CREATE SCHEMA IF NOT EXISTS hasn_sync;
COMMENT ON SCHEMA hasn_sync IS 'HASN IM 同步内核 schema（sync 事件/收件箱/owner revision·append_event 入口·doc16 §3.1/§3.2）';

-- 三个服务角色（本地演练建为 NOLOGIN 组角色，仅承载 grant 边界；生产窗口按 §10.3 建 LOGIN 并配密码）。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'astra_im_service') THEN
        CREATE ROLE astra_im_service NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'astra_sync_service') THEN
        CREATE ROLE astra_sync_service NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'astra_python_backend') THEN
        CREATE ROLE astra_python_backend NOLOGIN;
    END IF;
END $$;

COMMENT ON ROLE astra_im_service    IS 'IM use case 角色：hasn_im.* 全 DML + 身份只读投影 SELECT；禁 hasn_sync DML/其他业务 DML（doc16 §3.2）';
COMMENT ON ROLE astra_sync_service  IS 'sync pull/retention/worker 角色：事件只读清理、inbox DML；禁直接创建下行事件与读取业务表（doc16 §3.2）';
COMMENT ON ROLE astra_python_backend IS '普通业务/生产方角色：业务 schema DML；hasn_im 只读运营视图；禁 hasn_im/hasn_sync 表 DML；仅 EXECUTE hasn_sync.append_event（doc16 §3.2）';

-- ═══════════════════════════════════════════════════════════════════════════
-- ② 字段漂移收敛（R0-01 裁决执行·§10.3 步骤 5 前半）
--    dev 库当前默认值已与目标一致（mention_all=false / trust_level=1），以下为幂等安全兜底，
--    使迁移对齐生产（生产 snapshot 若漂移，本步统一到裁决值）。
-- ═══════════════════════════════════════════════════════════════════════════

-- hasn_messages.mention_all：裁决 default=false（@全体须写点显式传 true，禁默认 True 误伤·R0-01）
ALTER TABLE public.hasn_messages ALTER COLUMN mention_all SET DEFAULT false;
-- hasn_contacts.trust_level：裁决 default=1（陌生人 fail-closed·R0-01）
ALTER TABLE public.hasn_contacts ALTER COLUMN trust_level SET DEFAULT 1;
-- metadata.create_all 基线不会携带 codegen SQL 里的复合唯一约束和 server default；
-- 若不显式补齐，联系人接受/控制边投影的 ON CONFLICT 会在干净 R3 库直接 500。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.hasn_contacts'::regclass
          AND conname = 'uq_hasn_contact_relation'
    ) THEN
        ALTER TABLE public.hasn_contacts
            ADD CONSTRAINT uq_hasn_contact_relation
            UNIQUE (owner_id, peer_id, relation_type);
    END IF;
END
$$;
ALTER TABLE public.hasn_contacts
    ALTER COLUMN custom_permissions SET DEFAULT '{}'::jsonb,
    ALTER COLUMN created_time SET DEFAULT now();

-- ═══════════════════════════════════════════════════════════════════════════
-- ③ §10.2 成员周期 epoch + read_seq 回填（移表前，操作 public.* 原名）
--    权威 = seq + 可见区间 + read_seq；不以 unread_count 盲算事实（不可映射者入异常清单）。
-- ═══════════════════════════════════════════════════════════════════════════

-- 3a. 成员周期表先补齐本次回填读取/写入的全部字段。R2-11 必须自包含，不能依赖按日期
-- 排在本文件之后的迁移，否则生产 runner 会在回填 INSERT 时先撞 UndefinedColumn。
ALTER TABLE public.hasn_conversation_memberships
    ADD COLUMN IF NOT EXISTS member_star_id VARCHAR(40) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS member_name VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS muted BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS invited_by VARCHAR(40) NULL,
    ADD COLUMN IF NOT EXISTS charter_updated_time TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS history_complete_from_seq BIGINT NULL;
COMMENT ON COLUMN public.hasn_conversation_memberships.member_star_id IS
    '成员唤星号展示快照；成员身份权威仍是 member_hasn_id + epoch';
COMMENT ON COLUMN public.hasn_conversation_memberships.member_name IS
    '成员名称展示快照；不作为成员身份或判权依据';
COMMENT ON COLUMN public.hasn_conversation_memberships.muted IS
    '本成员在当前会话的免打扰设置';
COMMENT ON COLUMN public.hasn_conversation_memberships.invited_by IS
    '邀请该成员进入当前周期的 hasn_id';
COMMENT ON COLUMN public.hasn_conversation_memberships.charter_updated_time IS
    '分身群内发言准则最后更新时间';
COMMENT ON COLUMN public.hasn_conversation_memberships.history_complete_from_seq IS
    '成员周期完整性起点 seq（回填时 join time 缺失→保守边界=1，记录此起点；历史退群曾物删行，完整性从 cutover 起保证·§10.2）';

-- 3b. read_seq 回填异常清单（last_read_msg_id 不可映射到本会话消息 seq 者入此，NOT 盲算 unread·§10.2）
CREATE TABLE IF NOT EXISTS hasn_im.membership_read_backfill_exceptions (
    id               BIGSERIAL PRIMARY KEY,
    conversation_id  UUID        NOT NULL,
    member_hasn_id   VARCHAR(40) NOT NULL,
    last_read_msg_id BIGINT,
    reason           VARCHAR(40) NOT NULL,
    created_time     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE hasn_im.membership_read_backfill_exceptions IS
    'R2-11 read_seq 回填异常清单：last_read_msg_id 无法映射到本会话消息 seq 的记录（人工核对·不盲算 unread·§10.2）';

-- 3c. direct 会话双方永久 epoch：present from creation → joined_seq=1（可见全部历史）、read_seq=0（=joined_seq-1）
INSERT INTO public.hasn_conversation_memberships
    (conversation_id, member_hasn_id, member_type, role, joined_seq, left_seq, read_seq, state, joined_at, created_time)
SELECT c.id, p.member_hasn_id, p.member_type, 'member', 1, NULL, 0, 'active', c.created_time, now()
FROM public.hasn_conversations c
CROSS JOIN LATERAL (VALUES
    (c.participant_a_id, COALESCE(c.participant_a_type, 'human')),
    (c.participant_b_id, COALESCE(c.participant_b_type, 'human'))
) AS p(member_hasn_id, member_type)
WHERE c.type = 'direct'
  AND p.member_hasn_id IS NOT NULL AND p.member_hasn_id <> ''
  AND NOT EXISTS (
      SELECT 1 FROM public.hasn_conversation_memberships m
      WHERE m.conversation_id = c.id AND m.member_hasn_id = p.member_hasn_id AND m.left_seq IS NULL
  );

-- 3d. group 会话当前成员 epoch：有 join time → 首个可见 seq（received_at >= joined_at）；否则保守边界 1
INSERT INTO public.hasn_conversation_memberships
    (conversation_id, member_hasn_id, member_type, role, joined_seq, left_seq, read_seq, state,
     agent_group_trust_level, agent_charter, member_star_id, member_name, muted, invited_by,
     charter_updated_time, joined_at, created_time, history_complete_from_seq)
SELECT
    gm.conversation_id,
    gm.member_id,
    COALESCE(gm.member_type, 'human'),
    COALESCE(gm.role, 'member'),
    COALESCE(
        (SELECT MIN(msg.conversation_seq) FROM public.hasn_messages msg
          WHERE msg.conversation_id = gm.conversation_id
            AND gm.joined_at IS NOT NULL
            AND msg.server_received_at >= gm.joined_at),
        CASE WHEN gm.joined_at IS NOT NULL
             THEN COALESCE((SELECT c2.current_seq FROM public.hasn_conversations c2 WHERE c2.id = gm.conversation_id), 0) + 1
             ELSE 1 END
    ) AS joined_seq,
    NULL,
    0,
    'active',
    COALESCE(gm.agent_group_trust_level, 2),
    gm.agent_charter,
    COALESCE(gm.member_star_id, ''),
    COALESCE(gm.member_name, ''),
    COALESCE(gm.muted, false),
    gm.invited_by,
    gm.charter_updated_time,
    gm.joined_at,
    now(),
    CASE WHEN gm.joined_at IS NULL THEN 1 ELSE NULL END
FROM public.hasn_group_members gm
WHERE gm.conversation_id IS NOT NULL
  AND gm.member_id IS NOT NULL AND gm.member_id <> ''
  AND NOT EXISTS (
      SELECT 1 FROM public.hasn_conversation_memberships m
      WHERE m.conversation_id = gm.conversation_id AND m.member_hasn_id = gm.member_id AND m.left_seq IS NULL
  );

-- 3e. 已有活动周期也要补齐群名册展示/策略字段；不能只照顾上一步新插入的周期。
UPDATE public.hasn_conversation_memberships m
SET member_star_id = gm.member_star_id,
    member_name = gm.member_name,
    muted = gm.muted,
    invited_by = gm.invited_by,
    charter_updated_time = gm.charter_updated_time
FROM public.hasn_group_members gm
WHERE m.conversation_id = gm.conversation_id
  AND m.member_hasn_id = gm.member_id
  AND m.left_seq IS NULL;

-- 3f. read_seq 映射：last_read_msg_id → 该消息 conversation_seq（§4.3 clamp：单调只进 + 不越 joined 可见下界）
UPDATE public.hasn_conversation_memberships m
SET read_seq = mapped.seq
FROM (
    SELECT uc.conversation_id, uc.hasn_id, msg.conversation_seq AS seq
    FROM public.hasn_unread_counts uc
    JOIN public.hasn_messages msg
      ON msg.id = uc.last_read_msg_id AND msg.conversation_id = uc.conversation_id
    WHERE uc.last_read_msg_id IS NOT NULL AND uc.last_read_msg_id > 0
) mapped
WHERE m.conversation_id = mapped.conversation_id
  AND m.member_hasn_id = mapped.hasn_id
  AND m.left_seq IS NULL
  AND mapped.seq > m.read_seq
  AND mapped.seq >= m.joined_seq;

-- 3g. 异常清单：last_read_msg_id 存在但映射不到本会话消息（删档/跨会话）→ 入清单，不动 read_seq
INSERT INTO hasn_im.membership_read_backfill_exceptions (conversation_id, member_hasn_id, last_read_msg_id, reason)
SELECT uc.conversation_id, uc.hasn_id, uc.last_read_msg_id, 'msg_not_found_in_conversation'
FROM public.hasn_unread_counts uc
LEFT JOIN public.hasn_messages msg
  ON msg.id = uc.last_read_msg_id AND msg.conversation_id = uc.conversation_id
WHERE uc.last_read_msg_id IS NOT NULL AND uc.last_read_msg_id > 0
  AND msg.id IS NULL;

-- ═══════════════════════════════════════════════════════════════════════════
-- ④ 移表（§10.3 步骤 6）：IM 表 → hasn_im（保留名）；事件表 → hasn_im（去 hasn_im_ 前缀）；
--    sync 表 → hasn_sync（保留名）。owned sequence/index/constraint 随表自动迁移。
-- ═══════════════════════════════════════════════════════════════════════════

-- 4a. IM 归属表 → hasn_im（保留原名，与 §4.1 示例 hasn_im.hasn_conversations 一致）
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'hasn_messages', 'hasn_conversations', 'hasn_conversation_memberships',
        'hasn_unread_projection', 'hasn_group_agent_invites', 'hasn_suppressed_messages',
        'hasn_asset_grants', 'hasn_contacts', 'hasn_contact_requests',
        'agent_communication_settings',
        -- doc03 跨设备消息历史恢复的物化快照（2026-07-29 新增）。模型按 IM_SCHEMA 解析，
        -- 切换后代码找 hasn_im.*；漏搬会让 /sync/im/bootstrap/start 直接 500，
        -- daemon 换设备/离线后的历史补拉全部失败。
        'hasn_im_history_snapshots',
        'hasn_im_history_snapshot_conversations',
        'hasn_im_history_snapshot_messages'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = t) THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA hasn_im', t);
        END IF;
    END LOOP;
END $$;

-- 4b. 集成事件/消费者表 → hasn_im 并去 hasn_im_ 前缀（R2-04/05 已明注「R2-11 去前缀」）
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'hasn_im_integration_events') THEN
        ALTER TABLE public.hasn_im_integration_events SET SCHEMA hasn_im;
        ALTER TABLE hasn_im.hasn_im_integration_events RENAME TO integration_events;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'hasn_im_event_consumer_offsets') THEN
        ALTER TABLE public.hasn_im_event_consumer_offsets SET SCHEMA hasn_im;
        ALTER TABLE hasn_im.hasn_im_event_consumer_offsets RENAME TO event_consumer_offsets;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'hasn_im_event_consumer_failures') THEN
        ALTER TABLE public.hasn_im_event_consumer_failures SET SCHEMA hasn_im;
        ALTER TABLE hasn_im.hasn_im_event_consumer_failures RENAME TO event_consumer_failures;
    END IF;
END $$;

-- 4c. sync 表 → hasn_sync（保留名）
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'hasn_sync_events') THEN
        ALTER TABLE public.hasn_sync_events SET SCHEMA hasn_sync;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'hasn_sync_inbox_events') THEN
        ALTER TABLE public.hasn_sync_inbox_events SET SCHEMA hasn_sync;
    END IF;
END $$;

-- metadata.create_all 的旧基线没有携带 codegen SQL 中的 inbox 复合唯一约束；
-- sync push 使用同一三列作为 ON CONFLICT arbiter，故切换迁移必须自包含补齐。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'hasn_sync.hasn_sync_inbox_events'::regclass
          AND conname = 'uq_hasn_sync_inbox_client_event'
    ) THEN
        ALTER TABLE hasn_sync.hasn_sync_inbox_events
            ADD CONSTRAINT uq_hasn_sync_inbox_client_event
            UNIQUE (owner_id, node_id, client_event_id);
    END IF;
END
$$;

-- ═══════════════════════════════════════════════════════════════════════════
-- ⑤ append_event 换源（sync 表已迁入 hasn_sync）：固定 search_path=pg_catalog,hasn_sync，
--    全限定 hasn_sync.hasn_sync_events。语义与 R2-07 一致（per-owner advisory lock +
--    (owner,producer,source_event_id) 幂等 + gapless revision），仅 INSERT/SELECT 目标换源。
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
-- SECURITY DEFINER：这是 §3.2「允许的跨域写 = 仅 EXECUTE append_event」的关键——astra_python_backend
-- 只被授予 EXECUTE、无 hasn_sync 表 DML，须以函数属主权限完成内部 INSERT，才不被自身权限挡下。
-- 固定 search_path（下）是 SECURITY DEFINER 的必备加固，杜绝 search_path 注入。
SECURITY DEFINER
SET search_path = pg_catalog, hasn_sync
AS $$
DECLARE
    v_existing_revision bigint;
    v_existing_event_id text;
    v_next_revision     bigint;
    v_event_id          text;
    v_occurred_at       timestamptz;
BEGIN
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

    PERFORM pg_advisory_xact_lock(hashtext(p_owner_id));

    IF p_producer IS NOT NULL THEN
        SELECT e.revision, e.event_id
          INTO v_existing_revision, v_existing_event_id
          FROM hasn_sync.hasn_sync_events e
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
      FROM hasn_sync.hasn_sync_events e
     WHERE e.owner_id = p_owner_id;

    v_event_id := 'se_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 24);
    v_occurred_at := coalesce(p_occurred_at, now());

    INSERT INTO hasn_sync.hasn_sync_events (
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
    'IM 同步事件唯一写入口（doc16 §3.2·R2-11 换源 hasn_sync.hasn_sync_events）：per-owner advisory lock 串行化 gapless revision 分配 + (owner,producer,source_event_id) 幂等去重；返回 (revision, event_id, deduped)';

-- ═══════════════════════════════════════════════════════════════════════════
-- ⑥ agent 通信设置回填（§3.1：hasn_agents.social_enabled/inbound_policy → hasn_im.agent_communication_settings）
--    hasn_agents 属身份表、留 public 不迁；本步只建投影表 + 回填，源列保留（供反向/对账）。
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS hasn_im.agent_communication_settings (
    agent_hasn_id  VARCHAR(40) PRIMARY KEY,
    social_enabled BOOLEAN     NOT NULL DEFAULT true,
    inbound_policy VARCHAR(20) NOT NULL DEFAULT 'auto',
    created_time   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_time   TIMESTAMPTZ NULL
);
COMMENT ON TABLE hasn_im.agent_communication_settings IS
    'Agent 通信设置（从 hasn_agents.social_enabled/inbound_policy 迁出·doc16 §3.1）';
COMMENT ON COLUMN hasn_im.agent_communication_settings.social_enabled IS 'Agent 是否开启社交通信（可被主人关停）';
COMMENT ON COLUMN hasn_im.agent_communication_settings.inbound_policy  IS '入站策略 (auto:自动/manual:人工审/off:关闭)';

INSERT INTO hasn_im.agent_communication_settings (agent_hasn_id, social_enabled, inbound_policy)
SELECT a.hasn_id, COALESCE(a.social_enabled, true), COALESCE(a.inbound_policy, 'auto')
FROM public.hasn_agents a
WHERE a.hasn_id IS NOT NULL AND a.hasn_id <> ''
ON CONFLICT (agent_hasn_id) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════════
-- ⑦ 消费者 cursor 初始化为切换时刻 event head（存量不重投影·§10.2 末条）
--    已存在的消费者位点 bump 到当前 integration event 最大 seq；新消费者由应用侧默认从 head 起。
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE hasn_im.event_consumer_offsets o
SET last_acked_seq = GREATEST(o.last_acked_seq,
                              (SELECT COALESCE(MAX(event_seq), 0) FROM hasn_im.integration_events)),
    updated_at = now();

-- ═══════════════════════════════════════════════════════════════════════════
-- ⑧ 最终 grants（§10.3 步骤 8 / §3.2 角色边界）
-- ═══════════════════════════════════════════════════════════════════════════

-- astra_im_service：hasn_im.* 全 DML + 身份只读投影 SELECT
GRANT USAGE ON SCHEMA hasn_im TO astra_im_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA hasn_im TO astra_im_service;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA hasn_im TO astra_im_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA hasn_im GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO astra_im_service;
GRANT SELECT ON public.hasn_agents, public.hasn_humans, public.hasn_assets,
                public.hasn_storage_objects, public.hasn_nodes,
                public.hasn_node_bindings
    TO astra_im_service;  -- 明确授权的身份/附件/路由/通知只读投影
GRANT SELECT, INSERT, UPDATE ON public.hasn_group_im_command_outbox
    TO astra_im_service;
GRANT USAGE, SELECT ON SEQUENCE public.hasn_group_im_command_outbox_id_seq
    TO astra_im_service;
GRANT USAGE ON SCHEMA hasn_client TO astra_im_service;
GRANT SELECT ON hasn_client.push_tokens TO astra_im_service;

-- astra_sync_service：事件只读/清理 + inbox DML（禁直接创建下行事件与读取业务表）
GRANT USAGE ON SCHEMA hasn_sync TO astra_sync_service;
-- 兼容迁移重复执行：先撤销早期候选版本授出的全表 DML，再按具名表最小授权。
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA hasn_sync FROM astra_sync_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA hasn_sync
    REVOKE ALL PRIVILEGES ON TABLES FROM astra_sync_service;
GRANT SELECT, DELETE ON hasn_sync.hasn_sync_events TO astra_sync_service;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON hasn_sync.hasn_sync_inbox_events TO astra_sync_service;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA hasn_sync TO astra_sync_service;

-- astra_python_backend：hasn_im 只读运营视图 + 仅 EXECUTE append_event；禁 hasn_im/hasn_sync 表 DML
GRANT USAGE ON SCHEMA public TO astra_python_backend;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO astra_python_backend;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO astra_python_backend;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO astra_python_backend;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO astra_python_backend;
-- 旧 group_members/unread_counts 已退役，普通业务角色不得继续写回旧事实。
REVOKE INSERT, UPDATE, DELETE ON public.hasn_group_members, public.hasn_unread_counts
    FROM astra_python_backend;

DO $$
DECLARE
    s text;
BEGIN
    FOR s IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname NOT IN ('public', 'hasn_im', 'hasn_sync', 'pg_catalog', 'information_schema')
          AND nspname NOT LIKE 'pg_toast%'
          AND nspname NOT LIKE 'pg_temp_%'
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO astra_python_backend', s);
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO astra_python_backend',
            s
        );
        EXECUTE format(
            'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO astra_python_backend',
            s
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA %I '
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO astra_python_backend',
            s
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA %I '
            'GRANT USAGE, SELECT ON SEQUENCES TO astra_python_backend',
            s
        );
    END LOOP;
END $$;

-- Python 角色不直接读取 IM 基表；后续确需运营读取时只对具名只读 VIEW 单独授权。
-- 显式 REVOKE 使本迁移从旧候选版本重复执行时也能收紧已经授出的宽权限。
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA hasn_im FROM astra_python_backend;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA hasn_im FROM astra_python_backend;
ALTER DEFAULT PRIVILEGES IN SCHEMA hasn_im
    REVOKE ALL PRIVILEGES ON TABLES FROM astra_python_backend;
GRANT USAGE ON SCHEMA hasn_sync TO astra_python_backend;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA hasn_sync FROM astra_python_backend;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA hasn_sync FROM astra_python_backend;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA hasn_sync FROM astra_python_backend;
REVOKE ALL ON FUNCTION hasn_sync.append_event(text, text, text, text, text, jsonb, text, text, timestamptz)
    FROM PUBLIC;
GRANT USAGE ON SCHEMA hasn_sync TO astra_im_service;
GRANT EXECUTE ON FUNCTION hasn_sync.append_event(text, text, text, text, text, jsonb, text, text, timestamptz)
    TO astra_python_backend, astra_im_service;

COMMIT;
