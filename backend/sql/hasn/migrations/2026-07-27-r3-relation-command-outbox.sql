-- R3：身份域写 Agent 时只登记命令，关系 relay 经 IM role 幂等创建控制边。

CREATE TABLE IF NOT EXISTS public.hasn_relation_command_outbox (
    id bigserial PRIMARY KEY,
    command_id varchar(40) NOT NULL,
    command_type varchar(64) NOT NULL,
    owner_hasn_id varchar(40) NOT NULL,
    peer_hasn_id varchar(40) NOT NULL,
    idempotency_key varchar(160) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    attempt_count integer NOT NULL DEFAULT 0,
    next_retry_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    last_error text,
    completed_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_hasn_relation_command_outbox_command_id UNIQUE (command_id),
    CONSTRAINT uq_hasn_relation_command_outbox_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_hasn_relation_command_outbox_type
        CHECK (command_type IN ('ensure_owner_agent_control_edge')),
    CONSTRAINT ck_hasn_relation_command_outbox_status
        CHECK (status IN ('pending', 'processing', 'completed', 'dead_letter')),
    CONSTRAINT ck_hasn_relation_command_outbox_attempt_count
        CHECK (attempt_count >= 0)
);

-- 开发/演练基线可能已由 metadata.create_all 建出同名表；生成 model 不携带数据库约束，
-- 上面的 CREATE TABLE IF NOT EXISTS 会整段跳过。必须逐项补齐，否则业务的
-- ON CONFLICT(idempotency_key) 在干净库会报“没有匹配的唯一约束”。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.hasn_relation_command_outbox'::regclass
          AND conname = 'uq_hasn_relation_command_outbox_command_id'
    ) THEN
        ALTER TABLE public.hasn_relation_command_outbox
            ADD CONSTRAINT uq_hasn_relation_command_outbox_command_id
            UNIQUE (command_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.hasn_relation_command_outbox'::regclass
          AND conname = 'uq_hasn_relation_command_outbox_idempotency'
    ) THEN
        ALTER TABLE public.hasn_relation_command_outbox
            ADD CONSTRAINT uq_hasn_relation_command_outbox_idempotency
            UNIQUE (idempotency_key);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.hasn_relation_command_outbox'::regclass
          AND conname = 'ck_hasn_relation_command_outbox_type'
    ) THEN
        ALTER TABLE public.hasn_relation_command_outbox
            ADD CONSTRAINT ck_hasn_relation_command_outbox_type
            CHECK (command_type IN ('ensure_owner_agent_control_edge'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.hasn_relation_command_outbox'::regclass
          AND conname = 'ck_hasn_relation_command_outbox_status'
    ) THEN
        ALTER TABLE public.hasn_relation_command_outbox
            ADD CONSTRAINT ck_hasn_relation_command_outbox_status
            CHECK (status IN ('pending', 'processing', 'completed', 'dead_letter'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.hasn_relation_command_outbox'::regclass
          AND conname = 'ck_hasn_relation_command_outbox_attempt_count'
    ) THEN
        ALTER TABLE public.hasn_relation_command_outbox
            ADD CONSTRAINT ck_hasn_relation_command_outbox_attempt_count
            CHECK (attempt_count >= 0);
    END IF;
END
$$;

-- 同一 metadata 基线还会把 DateTimeMixin 的 Python 默认建成“无 server default”；
-- Core INSERT 不实例化 ORM dataclass，必须由数据库自己填时间。先修复可能的存量空值，
-- 再锁定与生产 DDL 一致的 NOT NULL + now()。
UPDATE public.hasn_relation_command_outbox
SET created_time = COALESCE(created_time, now()),
    updated_time = COALESCE(updated_time, created_time, now())
WHERE created_time IS NULL OR updated_time IS NULL;

ALTER TABLE public.hasn_relation_command_outbox
    ALTER COLUMN created_time SET DEFAULT now(),
    ALTER COLUMN created_time SET NOT NULL,
    ALTER COLUMN updated_time SET DEFAULT now(),
    ALTER COLUMN updated_time SET NOT NULL;

COMMENT ON TABLE public.hasn_relation_command_outbox IS
    '身份事实投影为 IM 关系的可靠命令队列';
COMMENT ON COLUMN public.hasn_relation_command_outbox.command_id IS
    '命令公开标识';
COMMENT ON COLUMN public.hasn_relation_command_outbox.command_type IS
    '关系命令类型';
COMMENT ON COLUMN public.hasn_relation_command_outbox.owner_hasn_id IS
    '控制边主人 HASN ID';
COMMENT ON COLUMN public.hasn_relation_command_outbox.peer_hasn_id IS
    '主人名下分身 HASN ID';
COMMENT ON COLUMN public.hasn_relation_command_outbox.idempotency_key IS
    '跨重试稳定幂等键';
COMMENT ON COLUMN public.hasn_relation_command_outbox.status IS
    '投递状态：pending/processing/completed/dead_letter';
COMMENT ON COLUMN public.hasn_relation_command_outbox.attempt_count IS
    '已失败次数';
COMMENT ON COLUMN public.hasn_relation_command_outbox.next_retry_at IS
    '下次允许领取时间';
COMMENT ON COLUMN public.hasn_relation_command_outbox.lease_until IS
    '处理租约截止时间';
COMMENT ON COLUMN public.hasn_relation_command_outbox.last_error IS
    '最近一次失败诊断';
COMMENT ON COLUMN public.hasn_relation_command_outbox.completed_at IS
    '投递完成时间';
COMMENT ON COLUMN public.hasn_relation_command_outbox.created_time IS
    '记录创建时间';
COMMENT ON COLUMN public.hasn_relation_command_outbox.updated_time IS
    '状态更新时间';

CREATE INDEX IF NOT EXISTS idx_hasn_relation_command_outbox_claim
    ON public.hasn_relation_command_outbox (
        status,
        next_retry_at,
        lease_until
    );
