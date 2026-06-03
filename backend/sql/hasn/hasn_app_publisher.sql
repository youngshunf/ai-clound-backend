-- AI-Native 应用发行方所有权（设计 14-AI-Native应用平台/11 §7.4）
-- 补当前缺失的注册安全：developer_id ↔ app_id 所有权绑定；仅 publisher 可更新/暂停/下架自己的 App。
CREATE TABLE hasn_app_publisher (
    id             BIGSERIAL PRIMARY KEY,
    app_id         VARCHAR(64) NOT NULL UNIQUE,
    developer_id   VARCHAR(40) NOT NULL,
    publisher_type VARCHAR(16) NOT NULL DEFAULT 'third_party',
    status         VARCHAR(16) NOT NULL DEFAULT 'active',
    created_time   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_time   TIMESTAMPTZ
);

CREATE INDEX idx_app_publisher_developer ON hasn_app_publisher (developer_id);

COMMENT ON TABLE hasn_app_publisher IS 'AI-Native 应用发行方（所有权绑定）';
COMMENT ON COLUMN hasn_app_publisher.app_id IS '应用 ID (全局唯一)';
COMMENT ON COLUMN hasn_app_publisher.developer_id IS '开发者 hasn_id (列宽对齐 varchar(40))';
COMMENT ON COLUMN hasn_app_publisher.publisher_type IS '发行方类型 (first_party:官方:blue/third_party:第三方:purple)';
COMMENT ON COLUMN hasn_app_publisher.status IS '状态 (active:正常:green/suspended:暂停:orange/revoked:吊销:gray)';
