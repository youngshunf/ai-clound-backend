-- 用户可见的逻辑文件与文件夹目录项。
CREATE TABLE hasn_storage_entries (
    id                    BIGSERIAL     PRIMARY KEY,
    entry_id              VARCHAR(40)   NOT NULL,
    owner_hasn_id         VARCHAR(40)   NOT NULL,
    asset_id              VARCHAR(40),
    parent_entry_id       VARCHAR(40),
    entry_type            VARCHAR(16)   NOT NULL,
    display_name          VARCHAR(255)  NOT NULL,
    normalized_name       VARCHAR(255)  NOT NULL,
    system_category       VARCHAR(32),
    version               BIGINT        NOT NULL DEFAULT 1,
    created_time          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_time          TIMESTAMPTZ,
    CONSTRAINT ck_hasn_storage_entries_target CHECK (
        (entry_type = 'file' AND asset_id IS NOT NULL)
        OR (entry_type = 'folder' AND asset_id IS NULL)
    ),
    CONSTRAINT ck_hasn_storage_entries_parent_self CHECK (
        parent_entry_id IS NULL OR parent_entry_id <> entry_id
    ),
    CONSTRAINT ck_hasn_storage_entries_version CHECK (version > 0)
);

CREATE UNIQUE INDEX uq_hasn_storage_entries_id
    ON hasn_storage_entries (entry_id);
CREATE UNIQUE INDEX uq_hasn_storage_entries_asset
    ON hasn_storage_entries (asset_id)
    WHERE asset_id IS NOT NULL;
CREATE UNIQUE INDEX uq_hasn_storage_entries_root_name
    ON hasn_storage_entries (owner_hasn_id, normalized_name)
    WHERE parent_entry_id IS NULL;
CREATE UNIQUE INDEX uq_hasn_storage_entries_child_name
    ON hasn_storage_entries (owner_hasn_id, parent_entry_id, normalized_name)
    WHERE parent_entry_id IS NOT NULL;
CREATE INDEX idx_hasn_storage_entries_parent
    ON hasn_storage_entries (owner_hasn_id, parent_entry_id, entry_type);

COMMENT ON TABLE hasn_storage_entries IS '用户云存储逻辑目录项';
COMMENT ON COLUMN hasn_storage_entries.entry_id IS '目录项稳定 ID';
COMMENT ON COLUMN hasn_storage_entries.owner_hasn_id IS '所属主人 hasn_id';
COMMENT ON COLUMN hasn_storage_entries.asset_id IS '文件项关联的逻辑资产 ID';
COMMENT ON COLUMN hasn_storage_entries.parent_entry_id IS '父目录项 ID；根目录为空';
COMMENT ON COLUMN hasn_storage_entries.entry_type IS '目录项类型 (file:文件:blue/folder:文件夹:orange)';
COMMENT ON COLUMN hasn_storage_entries.display_name IS '用户可见名称';
COMMENT ON COLUMN hasn_storage_entries.normalized_name IS '服务端归一化后的冲突判定名称';
COMMENT ON COLUMN hasn_storage_entries.system_category IS '系统目录分类';
COMMENT ON COLUMN hasn_storage_entries.version IS '重命名与移动的乐观锁版本';
COMMENT ON COLUMN hasn_storage_entries.created_time IS '创建时间';
COMMENT ON COLUMN hasn_storage_entries.updated_time IS '更新时间';
