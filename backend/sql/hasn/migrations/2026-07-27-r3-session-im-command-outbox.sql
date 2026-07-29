-- R3：工作会话/应用完成卡生产方自有 transactional outbox。

CREATE TABLE IF NOT EXISTS public.hasn_session_im_command_outbox (
    id bigserial PRIMARY KEY,
    command_id varchar(40) NOT NULL
        CONSTRAINT uq_hasn_session_im_command_outbox_command_id UNIQUE,
    producer varchar(40) NOT NULL
        CONSTRAINT ck_hasn_session_im_command_outbox_producer
        CHECK (producer = 'session'),
    conversation_id uuid NOT NULL,
    command_type varchar(64) NOT NULL
        CONSTRAINT ck_hasn_session_im_command_outbox_type
        CHECK (command_type = 'send_message'),
    payload jsonb NOT NULL,
    payload_hash char(64) NOT NULL,
    idempotency_key varchar(160) NOT NULL
        CONSTRAINT uq_hasn_session_im_command_outbox_idempotency UNIQUE,
    status varchar(16) NOT NULL DEFAULT 'pending'
        CONSTRAINT ck_hasn_session_im_command_outbox_status
        CHECK (status IN ('pending', 'processing', 'completed', 'dead_letter')),
    attempt_count integer NOT NULL DEFAULT 0
        CONSTRAINT ck_hasn_session_im_command_outbox_attempt_count
        CHECK (attempt_count >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    locked_by varchar(160),
    last_error text,
    message_id bigint,
    trace_id varchar(80),
    causation_id varchar(80),
    completed_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.hasn_session_im_command_outbox IS
    '工作会话结果与应用完成卡的事务 IM 命令队列';

CREATE INDEX IF NOT EXISTS idx_hasn_session_im_command_outbox_claim
    ON public.hasn_session_im_command_outbox
    (status, next_attempt_at, lease_until);
