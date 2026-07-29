-- 逻辑资产与业务资源的权威反向引用。
CREATE TABLE hasn_asset_bindings (
    id                 BIGSERIAL     PRIMARY KEY,
    binding_id         VARCHAR(40)   NOT NULL,
    owner_hasn_id      VARCHAR(40)   NOT NULL,
    asset_id           VARCHAR(40)   NOT NULL,
    resource_uri       VARCHAR(1024) NOT NULL,
    role               VARCHAR(32)   NOT NULL,
    status             VARCHAR(16)   NOT NULL DEFAULT 'active',
    created_time       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_time       TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_hasn_asset_bindings_id
    ON hasn_asset_bindings (binding_id);
CREATE UNIQUE INDEX uq_hasn_asset_bindings_resource
    ON hasn_asset_bindings (asset_id, resource_uri, role);
CREATE INDEX idx_hasn_asset_bindings_owner_status
    ON hasn_asset_bindings (owner_hasn_id, status);
CREATE INDEX idx_hasn_asset_bindings_asset_status
    ON hasn_asset_bindings (asset_id, status);

COMMENT ON TABLE hasn_asset_bindings IS '逻辑资产与业务资源的权威反向引用';
COMMENT ON COLUMN hasn_asset_bindings.binding_id IS '绑定稳定 ID';
COMMENT ON COLUMN hasn_asset_bindings.owner_hasn_id IS '资产所属主人 hasn_id';
COMMENT ON COLUMN hasn_asset_bindings.asset_id IS '逻辑资产 ID';
COMMENT ON COLUMN hasn_asset_bindings.resource_uri IS '引用资产的稳定资源 URI';
COMMENT ON COLUMN hasn_asset_bindings.role IS '引用角色';
COMMENT ON COLUMN hasn_asset_bindings.status IS '绑定状态 (active:有效:green/deleted:已删除:gray)';
COMMENT ON COLUMN hasn_asset_bindings.created_time IS '创建时间';
COMMENT ON COLUMN hasn_asset_bindings.updated_time IS '更新时间';
