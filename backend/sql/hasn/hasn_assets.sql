-- HASN 资产注册表（08 §1.x / 09 Stage1d）
-- 消息附件以 hasn://asset/{asset_id} 稳定引用承载，物理存储与业务引用解耦（07 D8 可移植）。
-- DB 只持 storage_id + object_key（不存 provider URL），换 provider/CDN 业务引用零改动。
CREATE TABLE hasn_assets (
    id                 BIGSERIAL    PRIMARY KEY,
    asset_id           VARCHAR(40)  NOT NULL,
    owner_hasn_id      VARCHAR(40)  NOT NULL,
    access             VARCHAR(16)  NOT NULL DEFAULT 'private',
    storage_id         BIGINT       NOT NULL,
    object_key         VARCHAR(1024) NOT NULL,
    kind               VARCHAR(16)  NOT NULL DEFAULT 'file',
    mime               VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream',
    size_bytes         BIGINT       NOT NULL DEFAULT 0,
    width              INT,
    height             INT,
    duration_ms        INT,
    transcript         TEXT,
    thumbnail_asset_id VARCHAR(40),
    extract_status     VARCHAR(16)  NOT NULL DEFAULT 'pending',
    created_time       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time       TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_hasn_assets_asset_id ON hasn_assets (asset_id);
CREATE INDEX idx_hasn_assets_owner ON hasn_assets (owner_hasn_id);

COMMENT ON TABLE hasn_assets IS 'HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）';
COMMENT ON COLUMN hasn_assets.asset_id IS '资产 ID (hasn://asset/{asset_id})';
COMMENT ON COLUMN hasn_assets.owner_hasn_id IS '所属主人 hasn_id';
COMMENT ON COLUMN hasn_assets.access IS '访问类型 (public:公开:green/private:私有:orange)';
COMMENT ON COLUMN hasn_assets.storage_id IS '存储空间 ID (关联 s3_storage.id)';
COMMENT ON COLUMN hasn_assets.object_key IS '对象 key (相对 opendal root，不含 provider URL)';
COMMENT ON COLUMN hasn_assets.kind IS '资产类型 (image:图片:blue/voice:语音:purple/file:文件:gray)';
COMMENT ON COLUMN hasn_assets.mime IS 'MIME 类型';
COMMENT ON COLUMN hasn_assets.size_bytes IS '字节大小';
COMMENT ON COLUMN hasn_assets.width IS '图片宽 (px)';
COMMENT ON COLUMN hasn_assets.height IS '图片高 (px)';
COMMENT ON COLUMN hasn_assets.duration_ms IS '语音时长 (毫秒)';
COMMENT ON COLUMN hasn_assets.transcript IS '语音转写文本 (STT 结果)';
COMMENT ON COLUMN hasn_assets.thumbnail_asset_id IS '缩略图资产 ID';
COMMENT ON COLUMN hasn_assets.extract_status IS '抽取状态 (pending:待处理:orange/done:完成:green/unsupported:不支持:gray/stt_unavailable:STT不可用:red)';
COMMENT ON COLUMN hasn_assets.created_time IS '创建时间';
COMMENT ON COLUMN hasn_assets.updated_time IS '更新时间';
