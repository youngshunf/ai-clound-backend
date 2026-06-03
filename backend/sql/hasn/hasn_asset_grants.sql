-- HASN 资产授权表（08 §1.6 跨 owner 越权洞关闭 / 09 Stage1d、1f）
-- 私有附件按「会话」授予读权：落消息时为附件写 grant，该会话参与者方可 resolve。
-- 鉴权 = requester 是 asset owner，或 requester ∈ 含该 asset grant 的会话参与者，否则 403。
CREATE TABLE hasn_asset_grants (
    id              BIGSERIAL    PRIMARY KEY,
    asset_id        VARCHAR(40)  NOT NULL,
    conversation_id UUID         NOT NULL,
    created_time    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time    TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_hasn_asset_grants_asset_conv ON hasn_asset_grants (asset_id, conversation_id);
CREATE INDEX idx_hasn_asset_grants_conv ON hasn_asset_grants (conversation_id);

COMMENT ON TABLE hasn_asset_grants IS 'HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）';
COMMENT ON COLUMN hasn_asset_grants.asset_id IS '资产 ID (关联 hasn_assets.asset_id)';
COMMENT ON COLUMN hasn_asset_grants.conversation_id IS '授权作用域会话 ID (该会话参与者可读该资产)';
COMMENT ON COLUMN hasn_asset_grants.created_time IS '创建时间';
COMMENT ON COLUMN hasn_asset_grants.updated_time IS '更新时间';
