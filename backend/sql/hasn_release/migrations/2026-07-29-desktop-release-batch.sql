-- 桌面端跨平台发布批次：云端分配版本、锁定 tag、保存 Git 历史与平台完成状态。
SET search_path TO hasn_release, public;

ALTER TABLE app_release
    ADD COLUMN IF NOT EXISTS release_tag varchar(64),
    ADD COLUMN IF NOT EXISTS previous_release_tag varchar(64),
    ADD COLUMN IF NOT EXISTS source_commit varchar(64),
    ADD COLUMN IF NOT EXISTS tag_status varchar(16) NOT NULL DEFAULT 'not_required',
    ADD COLUMN IF NOT EXISTS tag_created_time timestamptz,
    ADD COLUMN IF NOT EXISTS required_platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS completed_platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS release_commits jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS release_notes_status varchar(16) NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS release_notes_error text;

COMMENT ON COLUMN app_release.release_tag IS '云端锁定的 Git release tag，如 v0.3.1';
COMMENT ON COLUMN app_release.previous_release_tag IS '生成更新说明时使用的上一个真实 Git release tag';
COMMENT ON COLUMN app_release.source_commit IS 'release tag 锁定的 hasn-node Git commit';
COMMENT ON COLUMN app_release.tag_status IS 'tag 状态 (not_required:旧流程/pending:待推送/ready:已核验)';
COMMENT ON COLUMN app_release.tag_created_time IS 'release tag 经云端核验的时间';
COMMENT ON COLUMN app_release.required_platforms IS '正式发布要求的平台目标 JSON 数组';
COMMENT ON COLUMN app_release.completed_platforms IS 'installer 与 updater 均已上传的平台目标 JSON 数组';
COMMENT ON COLUMN app_release.release_commits IS '上一个 release tag 到本次 tag 的 Git 提交摘要';
COMMENT ON COLUMN app_release.release_notes_status IS '更新说明状态 (manual:人工/pending:待生成/ready:已生成/failed:生成失败)';
COMMENT ON COLUMN app_release.release_notes_error IS 'LLM 更新说明生成失败原因';

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_release_release_tag
    ON app_release (release_tag)
    WHERE release_tag IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_release_active_batch_channel
    ON app_release (channel)
    WHERE status = 'draft' AND release_tag IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_app_release_tag_status'
          AND conrelid = 'hasn_release.app_release'::regclass
    ) THEN
        ALTER TABLE app_release
            ADD CONSTRAINT ck_app_release_tag_status
            CHECK (tag_status IN ('not_required', 'pending', 'ready'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_app_release_notes_status'
          AND conrelid = 'hasn_release.app_release'::regclass
    ) THEN
        ALTER TABLE app_release
            ADD CONSTRAINT ck_app_release_notes_status
            CHECK (release_notes_status IN ('manual', 'pending', 'ready', 'failed'));
    END IF;
END
$$;
