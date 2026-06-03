-- AI-Native 应用实例（设计 14-AI-Native应用平台/11 §7.1）
-- 泛化 hasn_ragflow_instance：把"应用 → 后端部署实例"从知识库专用提升为平台一等概念。
-- 平台管实例「接入」(endpoint/接入凭据引用/scope/状态)，应用管实例「业务」(业务数据自持)。
CREATE TABLE hasn_app_instance (
    id                BIGSERIAL PRIMARY KEY,
    app_id            VARCHAR(64)  NOT NULL,
    scope             VARCHAR(16)  NOT NULL DEFAULT 'public',
    enterprise_id     BIGINT,
    endpoint          VARCHAR(512),
    transport_default VARCHAR(24)  NOT NULL DEFAULT 'cloud_relay',
    credential_ref    VARCHAR(255),
    status            VARCHAR(16)  NOT NULL DEFAULT 'active',
    config            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time      TIMESTAMPTZ
);

-- 每个 app 公共实例唯一；每个 (app, enterprise) 企业实例唯一（PG 部分唯一索引处理 null）
CREATE UNIQUE INDEX uq_app_instance_public
    ON hasn_app_instance (app_id) WHERE scope = 'public';
CREATE UNIQUE INDEX uq_app_instance_enterprise
    ON hasn_app_instance (app_id, enterprise_id) WHERE scope = 'enterprise';
CREATE INDEX idx_app_instance_app_status ON hasn_app_instance (app_id, status);

COMMENT ON TABLE hasn_app_instance IS 'AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）';
COMMENT ON COLUMN hasn_app_instance.app_id IS '应用 ID (关联 hasn_ai_native_app_manifest.app_id)';
COMMENT ON COLUMN hasn_app_instance.scope IS '作用域 (public:公共:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_app_instance.enterprise_id IS '企业 ID (scope=enterprise 必填；public 为 null)';
COMMENT ON COLUMN hasn_app_instance.endpoint IS '实例后端基地址 (gateway_internal 可空=云端自身)';
COMMENT ON COLUMN hasn_app_instance.transport_default IS '默认传输 (gateway_internal:就地:gray/cloud_relay:云端转发:blue/daemon_direct:本地直连:green/frontend_direct:前端直连:orange)';
COMMENT ON COLUMN hasn_app_instance.credential_ref IS '接入凭据引用 (指向加密凭据/KMS，不存明文)';
COMMENT ON COLUMN hasn_app_instance.status IS '状态 (active:可用:green/pending_config:待配置:orange/disabled:停用:gray)';
COMMENT ON COLUMN hasn_app_instance.config IS '实例配置';
