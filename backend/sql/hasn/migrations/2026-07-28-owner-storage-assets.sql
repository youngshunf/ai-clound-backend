-- 将 hasn_assets 扩展为逻辑资产层；旧物理列暂留用于灰度回滚。
ALTER TABLE hasn_assets
    ADD COLUMN IF NOT EXISTS object_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS category VARCHAR(32),
    ADD COLUMN IF NOT EXISTS original_name VARCHAR(512),
    ADD COLUMN IF NOT EXISTS source_app VARCHAR(64),
    ADD COLUMN IF NOT EXISTS upload_idempotency_key VARCHAR(128),
    ADD COLUMN IF NOT EXISTS derived_from_asset_id VARCHAR(40),
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(24) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS trashed_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_hasn_assets_owner_lifecycle
    ON hasn_assets (owner_hasn_id, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_hasn_assets_object
    ON hasn_assets (object_id);
CREATE INDEX IF NOT EXISTS idx_hasn_assets_category_created
    ON hasn_assets (category, created_time);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_assets_owner_upload_idempotency
    ON hasn_assets (owner_hasn_id, upload_idempotency_key)
    WHERE upload_idempotency_key IS NOT NULL;

COMMENT ON COLUMN hasn_assets.object_id IS '物理对象 ID';
COMMENT ON COLUMN hasn_assets.category IS '业务资产类别';
COMMENT ON COLUMN hasn_assets.original_name IS '用户可见原始文件名';
COMMENT ON COLUMN hasn_assets.source_app IS '来源应用或平台模块';
COMMENT ON COLUMN hasn_assets.upload_idempotency_key IS 'Owner 范围内的上传幂等键';
COMMENT ON COLUMN hasn_assets.derived_from_asset_id IS '转存来源逻辑资产 ID';
COMMENT ON COLUMN hasn_assets.lifecycle_status IS '生命周期状态 (uploading:上传中:orange/active:可用:green/trashed:回收站:orange/deleting:删除中:orange/deleted:已删除:gray/error:异常:red)';
COMMENT ON COLUMN hasn_assets.trashed_time IS '进入回收站时间';
COMMENT ON COLUMN hasn_assets.deleted_time IS '逻辑资产确认删除时间';
COMMENT ON COLUMN hasn_assets.version IS '生命周期乐观锁版本';
