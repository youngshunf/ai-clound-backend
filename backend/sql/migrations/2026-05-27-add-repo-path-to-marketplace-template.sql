-- 为 marketplace_template 表添加同步相关字段
-- 日期: 2026-05-27
-- 说明: 添加 repo_path, git_commit_hash, synced_at, translated_at 字段

ALTER TABLE marketplace_template
ADD COLUMN IF NOT EXISTS repo_path VARCHAR(500),
ADD COLUMN IF NOT EXISTS git_commit_hash VARCHAR(64),
ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS translated_at TIMESTAMPTZ;

COMMENT ON COLUMN marketplace_template.repo_path IS '在 hasn-hub 中的路径';
COMMENT ON COLUMN marketplace_template.git_commit_hash IS '最新同步的 commit hash';
COMMENT ON COLUMN marketplace_template.synced_at IS '最后同步时间';
COMMENT ON COLUMN marketplace_template.translated_at IS '最后翻译时间';
