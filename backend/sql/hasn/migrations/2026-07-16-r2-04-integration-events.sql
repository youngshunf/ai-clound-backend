-- R2-04 · hasn_im.integration_events 只追加事件日志（event_seq 乱序防护·doc16 §7.2）
--
-- 只追加日志；event_seq 是全局消费位点，须防「取号顺序 ≠ 提交顺序」——序列取号在事务内发生，
-- 先取号的事务后提交时其 seq 已低于消费者水位、被永久跳过（把 §1.2「消息存在但事件不可见」以
-- 并发窗口重新引入）。故 event_seq **禁止**裸 BIGSERIAL+水位，改由 application 层同事务先
-- pg_advisory_xact_lock(shard) 再 MAX+1 分配（沿用 cloud 8a125cdf 先例，见 event_appender.py）。
--
-- R2 期物理表落 public（前缀 hasn_im_），R2-11 统一 SET SCHEMA → hasn_im 并去前缀。
-- 幂等：可反复执行。

CREATE TABLE IF NOT EXISTS public.hasn_im_integration_events (
    id             BIGSERIAL   PRIMARY KEY,
    event_seq      BIGINT      NOT NULL,
    event_id       VARCHAR(64) NOT NULL,
    event_type     VARCHAR(80) NOT NULL,
    aggregate_type VARCHAR(40) NOT NULL,
    aggregate_id   VARCHAR(64) NOT NULL,
    aggregate_seq  BIGINT,
    shard_key      INTEGER     NOT NULL DEFAULT 0,
    payload        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    trace_id       VARCHAR(64),
    causation_id   VARCHAR(64),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_time   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- (shard_key, event_seq) 唯一：既是「同分片 seq 不重复」的硬约束，也直接服务消费者按序拉取
-- （WHERE shard_key=:s AND event_seq > :cursor ORDER BY event_seq），故无需另建顺序索引。
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_im_int_events_shard_seq
    ON public.hasn_im_integration_events (shard_key, event_seq);

-- event_id 全局唯一（消费者/客户端按 event_id 去重·§7.4）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_im_int_events_event_id
    ON public.hasn_im_integration_events (event_id);

-- 按聚合根反查（如某会话的全部 committed 事件），运营/对账用。
CREATE INDEX IF NOT EXISTS idx_hasn_im_int_events_aggregate
    ON public.hasn_im_integration_events (aggregate_type, aggregate_id);

COMMENT ON TABLE  public.hasn_im_integration_events IS 'HASN IM 只追加集成事件日志（多消费者事件源·doc16 §7.2）';
COMMENT ON COLUMN public.hasn_im_integration_events.event_seq      IS '全局消费位点（advisory-lock 串行分配·禁裸 BIGSERIAL+水位·§7.2）';
COMMENT ON COLUMN public.hasn_im_integration_events.event_id       IS '事件唯一 ID（ULID/UUID·客户端去重键）';
COMMENT ON COLUMN public.hasn_im_integration_events.event_type     IS '版本化事件类型（如 im.message.committed.v1）';
COMMENT ON COLUMN public.hasn_im_integration_events.aggregate_type IS '聚合根类型（如 conversation）';
COMMENT ON COLUMN public.hasn_im_integration_events.aggregate_id   IS '聚合根 ID';
COMMENT ON COLUMN public.hasn_im_integration_events.aggregate_seq  IS '聚合内序号（如 conversation_seq），可空';
COMMENT ON COLUMN public.hasn_im_integration_events.shard_key      IS '分片键（初期恒 0·分片数=1；容量需要时按 hash(aggregate_id)%N 分片·表结构不变）';
COMMENT ON COLUMN public.hasn_im_integration_events.payload        IS '完整载荷（消费者不出 IM 域即可处理·禁把正文三份存储·§7.2）';
