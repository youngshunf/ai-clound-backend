-- R2-05 · 消费者框架两表（consumer offsets + failures·doc16 §7.2）
--
-- 每个消费者独立、按 event_seq 至少一次处理；处理成功与 cursor 推进（durable）在同一事务；
-- lease_owner/lease_until 保证同一 consumer_name 同一时刻单实例活跃（独立 worker 进程组）。
-- retention 低水位取所有有效 durable 消费者的最小 last_acked_seq。
--
-- R2 期物理表落 public（前缀 hasn_im_），R2-11 统一 SET SCHEMA → hasn_im 并去前缀。
-- 幂等：可反复执行。

CREATE TABLE IF NOT EXISTS public.hasn_im_event_consumer_offsets (
    consumer_name  VARCHAR(64)  PRIMARY KEY,
    last_acked_seq BIGINT       NOT NULL DEFAULT 0,
    lease_owner    VARCHAR(80),
    lease_until    TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- consumer_name + event_seq 唯一：同一消费者对同一事件的失败态只有一行。
CREATE TABLE IF NOT EXISTS public.hasn_im_event_consumer_failures (
    consumer_name    VARCHAR(64)  NOT NULL,
    event_seq        BIGINT       NOT NULL,
    attempts         INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ,
    last_error       TEXT,
    dead_lettered_at TIMESTAMPTZ,
    resolution       VARCHAR(20),
    created_time     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_time     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_name, event_seq)
);

-- 未决 dead letter 快查（retention/告警巡检）。
CREATE INDEX IF NOT EXISTS idx_hasn_im_consumer_failures_dead
    ON public.hasn_im_event_consumer_failures (consumer_name)
    WHERE dead_lettered_at IS NOT NULL AND resolution IS NULL;

COMMENT ON TABLE  public.hasn_im_event_consumer_offsets  IS 'IM 消费者位点与租约（单实例 lease·retention 低水位源·doc16 §7.2）';
COMMENT ON COLUMN public.hasn_im_event_consumer_offsets.last_acked_seq IS '已确认位点（durable 与处理同事务推进；retention 低水位取有效 durable 消费者最小值）';
COMMENT ON COLUMN public.hasn_im_event_consumer_offsets.lease_owner    IS '租约持有者实例 ID（同 consumer_name 同一时刻单实例活跃）';
COMMENT ON COLUMN public.hasn_im_event_consumer_offsets.lease_until    IS '租约到期时刻（过期后其它实例可抢占）';

COMMENT ON TABLE  public.hasn_im_event_consumer_failures IS 'IM 消费者失败态（durable 重试/dead letter·best-effort 不写此表·doc16 §7.2）';
COMMENT ON COLUMN public.hasn_im_event_consumer_failures.attempts         IS '已失败次数';
COMMENT ON COLUMN public.hasn_im_event_consumer_failures.next_attempt_at  IS '下次重试时刻（退避）';
COMMENT ON COLUMN public.hasn_im_event_consumer_failures.dead_lettered_at IS '进 dead letter 时刻（须显式授权修复重放/确认跳过才能推进）';
COMMENT ON COLUMN public.hasn_im_event_consumer_failures.resolution       IS 'dead letter 处置 (replayed:修复后重放/skipped:确认跳过·NULL=未决)';
