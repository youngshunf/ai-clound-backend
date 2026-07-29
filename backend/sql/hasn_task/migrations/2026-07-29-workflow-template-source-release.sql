-- 为场景工作流补齐 AstraHub 官方制品与来源审计字段。
ALTER TABLE hasn_task.workflow_template
    ADD COLUMN IF NOT EXISTS package_url VARCHAR(500),
    ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(128),
    ADD COLUMN IF NOT EXISTS file_size INTEGER,
    ADD COLUMN IF NOT EXISTS source_repo_path VARCHAR(500),
    ADD COLUMN IF NOT EXISTS git_commit_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ;

COMMENT ON COLUMN hasn_task.workflow_template.package_url IS '官方发布制品下载 URL';
COMMENT ON COLUMN hasn_task.workflow_template.file_hash IS '官方发布 ZIP SHA256';
COMMENT ON COLUMN hasn_task.workflow_template.content_hash IS '官方发布源文件清单指纹';
COMMENT ON COLUMN hasn_task.workflow_template.file_size IS '官方发布 ZIP 字节数';
COMMENT ON COLUMN hasn_task.workflow_template.source_repo_path IS '官方 Hub 仓库内相对路径';
COMMENT ON COLUMN hasn_task.workflow_template.git_commit_hash IS '官方 Hub 发布 commit';
COMMENT ON COLUMN hasn_task.workflow_template.synced_at IS '最近一次官方发布同步时间';
