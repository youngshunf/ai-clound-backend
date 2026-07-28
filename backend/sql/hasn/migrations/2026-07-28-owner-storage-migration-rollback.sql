-- 迁移回滚必须恢复原键布局，不能把 owner_scoped 对象误标为 legacy。
ALTER TABLE hasn_storage_migration_items
    ADD COLUMN IF NOT EXISTS source_key_layout VARCHAR(24);

UPDATE hasn_storage_migration_items AS i
SET source_key_layout = o.key_layout
FROM hasn_storage_objects AS o
WHERE o.object_id = i.object_id
  AND i.source_key_layout IS NULL;

ALTER TABLE hasn_storage_migration_items
    ALTER COLUMN source_key_layout SET NOT NULL;

COMMENT ON COLUMN hasn_storage_migration_items.source_key_layout IS
    '迁移前对象键布局（精确回滚依据）';

ALTER TABLE hasn_storage_migration_items
    ADD COLUMN IF NOT EXISTS source_cleanup_status VARCHAR(24)
        NOT NULL DEFAULT 'retained',
    ADD COLUMN IF NOT EXISTS source_deleted_time TIMESTAMPTZ;

COMMENT ON COLUMN hasn_storage_migration_items.source_cleanup_status IS
    '源对象清理状态 (retained:观察期保留:blue/deleted:已清理:green/shared:跨主人共享保留:orange/failed:清理失败:red)';
COMMENT ON COLUMN hasn_storage_migration_items.source_deleted_time IS
    '源对象确认删除时间';
