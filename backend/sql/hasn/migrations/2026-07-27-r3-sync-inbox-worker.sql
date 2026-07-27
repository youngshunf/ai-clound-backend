-- R3 sync inbox worker：为跨库两事务编排补充可领取、可重试、可终结的状态。
--
-- 本迁移兼容 R2-11 切换前后的表位置，但只允许命中一个权威表。业务 handler 的提交与
-- sync ACK 无法组成跨库原子事务，因此 worker 采用「先提交 inbox、幂等应用业务、最后 ACK」
-- 的至少一次语义；租约字段保证进程崩溃后可重新领取。

BEGIN;

DO $$
DECLARE
    v_table regclass;
BEGIN
    v_table := COALESCE(
        to_regclass('hasn_sync.hasn_sync_inbox_events'),
        to_regclass('public.hasn_sync_inbox_events')
    );
    IF v_table IS NULL THEN
        RAISE EXCEPTION 'R3 sync inbox worker：未找到 hasn_sync_inbox_events';
    END IF;

    EXECUTE format(
        'ALTER TABLE %s
         ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
         ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
         ADD COLUMN IF NOT EXISTS locked_by varchar(64),
         ADD COLUMN IF NOT EXISTS locked_at timestamptz,
         ADD COLUMN IF NOT EXISTS last_error text,
         ADD COLUMN IF NOT EXISTS applied_at timestamptz,
         ADD COLUMN IF NOT EXISTS dead_at timestamptz',
        v_table
    );
    -- metadata.create_all 的过渡基线可能已有 attempt_count 但没有服务端默认值；
    -- ADD COLUMN IF NOT EXISTS 不会修复既有列，必须显式收敛为 worker 所需契约。
    EXECUTE format(
        'ALTER TABLE %s ALTER COLUMN attempt_count SET DEFAULT 0',
        v_table
    );
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_hasn_sync_inbox_worker_claim
         ON %s (status, next_attempt_at, locked_at, received_at, id)',
        v_table
    );

    EXECUTE format(
        'COMMENT ON COLUMN %s.attempt_count IS %L',
        v_table,
        '业务应用尝试次数；每次领取原子加一'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.next_attempt_at IS %L',
        v_table,
        '失败后的下次可领取时间'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.locked_by IS %L',
        v_table,
        '当前领取该事件的 worker 实例 ID'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.locked_at IS %L',
        v_table,
        '当前 worker 租约起始时间'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.last_error IS %L',
        v_table,
        '最近一次业务应用失败摘要'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.applied_at IS %L',
        v_table,
        '业务写已提交且 sync ACK 已落库的时间'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.dead_at IS %L',
        v_table,
        '重试耗尽进入 dead 状态的时间'
    );
    EXECUTE format(
        'COMMENT ON COLUMN %s.status IS %L',
        v_table,
        '处理状态（accepted/processing/retry/applied/dead/conflict）'
    );
END $$;

COMMIT;
