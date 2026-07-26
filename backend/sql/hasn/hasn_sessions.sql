-- hasn_sessions 表
CREATE TABLE hasn_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64),
    owner_id VARCHAR(64) NOT NULL,
    hasn_id VARCHAR(64) NOT NULL,
    session_kind VARCHAR(20) NOT NULL,
    session_scope VARCHAR(20) NOT NULL,
    session_status VARCHAR(20) NOT NULL DEFAULT 'active',
    parent_session_id VARCHAR(64),
    continuation_from_session_id VARCHAR(64),
    origin_type VARCHAR(20) NOT NULL,
    origin_ref VARCHAR(200),
    title VARCHAR(500),
    summary_checkpoint_json JSONB DEFAULT '{}',
    active_binding_id VARCHAR(64),
    last_message_id VARCHAR(64),
    last_message_at TIMESTAMPTZ,
    created_time TIMESTAMPTZ DEFAULT NOW(),
    updated_time TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    CONSTRAINT chk_session_kind CHECK (
        session_kind IN ('interactive', 'task', 'temporary', 'external', 'system')
    ),
    CONSTRAINT chk_session_scope CHECK (
        session_scope IN ('conversation_visible', 'summary_only', 'local_only')
    ),
    CONSTRAINT chk_session_status CHECK (
        session_status IN ('active', 'waiting_for_user', 'completed', 'error', 'cancelled')
    ),
    CONSTRAINT chk_origin_type CHECK (
        origin_type IN ('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', 'app', 'manual', 'copilot')
    )
);

CREATE INDEX idx_hasn_sessions_owner ON hasn_sessions(owner_id);
CREATE INDEX idx_hasn_sessions_hasn ON hasn_sessions(hasn_id);
CREATE INDEX idx_hasn_sessions_conversation ON hasn_sessions(conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX idx_hasn_sessions_kind_scope ON hasn_sessions(session_kind, session_scope);
CREATE INDEX idx_hasn_sessions_status ON hasn_sessions(session_status);
CREATE INDEX idx_hasn_sessions_updated ON hasn_sessions(updated_time DESC);

COMMENT ON TABLE hasn_sessions IS 'Session 投影表（云端）';
COMMENT ON COLUMN hasn_sessions.session_id IS 'Session 全局唯一 ID（ULID 格式）';
COMMENT ON COLUMN hasn_sessions.conversation_id IS '可空；只有绑定到主题线程时填写';
COMMENT ON COLUMN hasn_sessions.owner_id IS '所属 Owner ID';
COMMENT ON COLUMN hasn_sessions.hasn_id IS '所属 Agent ID';
COMMENT ON COLUMN hasn_sessions.session_kind IS 'Session 类型 (interactive:交互式:blue/task:任务:green/temporary:临时:gray/external:外部:orange/system:系统:purple)';
COMMENT ON COLUMN hasn_sessions.session_scope IS '同步范围 (conversation_visible:会话可见:blue/summary_only:仅摘要:green/local_only:仅本地:gray)';
COMMENT ON COLUMN hasn_sessions.session_status IS 'Session 状态 (active:活跃:blue/completed:已完成:green/error:错误:red/cancelled:已取消:gray)';
COMMENT ON COLUMN hasn_sessions.parent_session_id IS '父 Session ID（用于嵌套 Session）';
COMMENT ON COLUMN hasn_sessions.continuation_from_session_id IS '续接自哪个 Session';
COMMENT ON COLUMN hasn_sessions.origin_type IS '来源类型 (ui:用户界面:blue/task_run:任务运行:green/external_app:外部应用:orange/system:系统:purple)';
COMMENT ON COLUMN hasn_sessions.origin_ref IS '来源引用，如 task_run.id、external_request_id';
COMMENT ON COLUMN hasn_sessions.title IS 'Session 标题';
COMMENT ON COLUMN hasn_sessions.summary_checkpoint_json IS '摘要快照，用于跨设备恢复（不是完整历史）';
COMMENT ON COLUMN hasn_sessions.active_binding_id IS '当前活跃的绑定 ID';
COMMENT ON COLUMN hasn_sessions.last_message_id IS '最后一条消息 ID';
COMMENT ON COLUMN hasn_sessions.last_message_at IS '最后一条消息时间';
COMMENT ON COLUMN hasn_sessions.created_time IS '创建时间';
COMMENT ON COLUMN hasn_sessions.updated_time IS '更新时间';
COMMENT ON COLUMN hasn_sessions.closed_at IS '关闭时间';
