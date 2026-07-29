-- 本地优先产物只在主人显式上传后保存私有桶快照。
-- 三字段全空表示尚未上传；全非空表示可验证的同一份快照。

ALTER TABLE public.hasn_artifacts
    ADD COLUMN IF NOT EXISTS source_asset_uri VARCHAR(512) NULL,
    ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS source_synced_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN public.hasn_artifacts.source_asset_uri IS
    '本地原件显式上传后的 hasn://asset/{id} 快照引用；NULL 表示尚未上传';
COMMENT ON COLUMN public.hasn_artifacts.source_hash IS
    '已上传快照的 64 位 sha256；必须与 source_asset_uri 指向的字节一致';
COMMENT ON COLUMN public.hasn_artifacts.source_synced_at IS
    '已上传快照的上传时间';

ALTER TABLE public.hasn_artifacts
    DROP CONSTRAINT IF EXISTS ck_hasn_artifacts_source_snapshot_atomic;

ALTER TABLE public.hasn_artifacts
    ADD CONSTRAINT ck_hasn_artifacts_source_snapshot_atomic CHECK (
        (
            source_asset_uri IS NULL
            AND source_hash IS NULL
            AND source_synced_at IS NULL
        )
        OR
        (
            source_asset_uri IS NOT NULL
            AND source_hash IS NOT NULL
            AND source_synced_at IS NOT NULL
        )
    );
