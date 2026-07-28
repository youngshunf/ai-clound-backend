-- 私有与公开对象的物理访问策略不同，内容去重键必须包含 access。
DROP INDEX IF EXISTS uq_hasn_storage_objects_owner_sha256;

CREATE UNIQUE INDEX uq_hasn_storage_objects_owner_sha256
    ON hasn_storage_objects (owner_hasn_id, sha256, access)
    WHERE billable_to_owner AND state <> 'deleted' AND sha256 IS NOT NULL;
