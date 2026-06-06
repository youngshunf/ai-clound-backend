-- AI-Native App 用户级接入凭据（设计 14-AI-Native应用平台/实施/03 §2.1）
-- 泛化 hasn_ragflow_credential：把"用户 × 实例 × 接入密文 × 状态"从知识库专用提升为平台通用。
-- RAGFlow 私有字段（ragflow_user_id/ragflow_tenant_id）下沉进 config，不再占专属列。
CREATE TABLE hasn_app_credential (
    id              BIGSERIAL    PRIMARY KEY,
    app_id          VARCHAR(64)  NOT NULL,
    user_id         BIGINT       NOT NULL,
    app_instance_id BIGINT       NOT NULL,
    credential_ref  VARCHAR      NOT NULL DEFAULT '',
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',
    last_error      TEXT,
    config          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_time    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time    TIMESTAMPTZ
);

-- 同一用户在同一实例至多一条凭据（幂等键）；按 (app, user) 查询加速
CREATE UNIQUE INDEX uq_app_credential_user_instance ON hasn_app_credential (user_id, app_instance_id);
CREATE INDEX        idx_app_credential_app_user     ON hasn_app_credential (app_id, user_id);

COMMENT ON TABLE  hasn_app_credential                 IS 'AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）';
COMMENT ON COLUMN hasn_app_credential.app_id          IS '应用 ID（如 knowledge）';
COMMENT ON COLUMN hasn_app_credential.user_id         IS '用户 ID';
COMMENT ON COLUMN hasn_app_credential.app_instance_id IS '所属应用实例 ID（hasn_app_instance.id）';
COMMENT ON COLUMN hasn_app_credential.credential_ref  IS '用户级凭据密文（key_encryption.encrypt，绝不存明文）';
COMMENT ON COLUMN hasn_app_credential.status          IS '状态 (pending:待激活:gray/active:已激活:green/revoked:已吊销:red/error:错误:orange)';
COMMENT ON COLUMN hasn_app_credential.last_error      IS '最近一次 provision/刷新错误';
COMMENT ON COLUMN hasn_app_credential.config          IS 'app 私有凭据元数据（如 ragflow_user_id/ragflow_tenant_id）';
