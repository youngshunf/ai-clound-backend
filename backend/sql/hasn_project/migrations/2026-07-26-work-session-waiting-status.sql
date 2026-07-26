-- 项目聚合需要把「等待主人」视为活动工作会话；云端摘要状态必须与本地工作会话状态机一致。
-- 仅扩展既有约束，不改写任何已有会话，也不改变终态语义。

ALTER TABLE public.hasn_sessions
    DROP CONSTRAINT IF EXISTS chk_session_status;

ALTER TABLE public.hasn_sessions
    ADD CONSTRAINT chk_session_status
    CHECK (session_status IN ('active', 'waiting_for_user', 'completed', 'error', 'cancelled'));
