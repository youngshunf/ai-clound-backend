-- 桌面端发布与自动更新 hasn_release：3 张表（设计 docs/hasn-node设计文档/桌面端发布与自动更新/00 §3）。
-- 落 schema hasn_release（ADR-15 应用独立 schema）；PostgreSQL 语法。
--
-- 设计要旨（发布设计 §2/§3）：
--   - app_release   = 版本级（一个版本一行，携 changelog + 状态 + is_latest 指针）；
--   - release_asset = 平台资产（一版本多平台 × 两种包：installer=dmg 给下载页 / updater=app.tar.gz 给热更新）；
--   - release_build = CI 构建任务（GitHub Actions 构建进度，给管理端看状态）。
-- 单一事实源 = 云端 DB；二进制存七牛 CDN，DB 存元数据 + CDN url。
-- 签名私钥仅 CI 持有：updater 资产的 signature 由 CI/手动上传附带的 minisign 签名，服务端只验签不签名。

-- 应用独立 schema（ADR-15）。须先建 schema，否则 SET search_path 静默回落 public（codegen 误建 public）。
CREATE SCHEMA IF NOT EXISTS hasn_release;
SET search_path TO hasn_release, public;

-- ========== (1) app_release — 版本级（一个版本一行） ==========
CREATE TABLE IF NOT EXISTS app_release (
    id bigserial PRIMARY KEY,
    version varchar(32) NOT NULL,
    channel varchar(16) NOT NULL DEFAULT 'stable' CHECK (channel IN ('stable', 'beta')),
    release_notes_md text,
    release_notes_en_md text,
    status varchar(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'deprecated')),
    is_latest boolean NOT NULL DEFAULT false,
    source varchar(16) NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'github')),
    github_run_id varchar(64),
    release_tag varchar(64),
    previous_release_tag varchar(64),
    source_commit varchar(64),
    tag_status varchar(16) NOT NULL DEFAULT 'not_required'
        CHECK (tag_status IN ('not_required', 'pending', 'ready')),
    tag_created_time timestamptz,
    required_platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
    completed_platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
    release_commits jsonb NOT NULL DEFAULT '[]'::jsonb,
    release_notes_status varchar(16) NOT NULL DEFAULT 'manual'
        CHECK (release_notes_status IN ('manual', 'pending', 'ready', 'failed')),
    release_notes_error text,
    published_time timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE app_release IS '桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）';
COMMENT ON COLUMN app_release.version IS 'semver 版本号，如 1.2.0';
COMMENT ON COLUMN app_release.channel IS '发布渠道 (stable:稳定版:green/beta:内测版:orange)';
COMMENT ON COLUMN app_release.release_notes_md IS '更新日志 Markdown（中）';
COMMENT ON COLUMN app_release.release_notes_en_md IS '更新日志 Markdown（英）';
COMMENT ON COLUMN app_release.status IS '状态 (draft:草稿:gray/published:已发布:green/deprecated:已下线:slate)';
COMMENT ON COLUMN app_release.is_latest IS '是否当前 channel 最新（置新版时把同 channel 旧版落 false）';
COMMENT ON COLUMN app_release.source IS '来源 (manual:手动上传:blue/github:GitHub构建:purple)';
COMMENT ON COLUMN app_release.github_run_id IS '关联 GitHub Actions run id（source=github 时）';
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
COMMENT ON COLUMN app_release.published_time IS '发布时刻（status 转 published 时写入）';
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_release_version_channel ON app_release (version, channel);
CREATE INDEX IF NOT EXISTS idx_app_release_latest ON app_release (channel, is_latest, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_release_release_tag ON app_release (release_tag) WHERE release_tag IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_release_active_batch_channel
    ON app_release (channel)
    WHERE status = 'draft' AND release_tag IS NOT NULL;

-- ========== (2) release_asset — 平台资产（一版本多平台 × 两种包） ==========
CREATE TABLE IF NOT EXISTS release_asset (
    id bigserial PRIMARY KEY,
    release_id bigint NOT NULL REFERENCES app_release (id) ON DELETE CASCADE,
    platform_target varchar(32) NOT NULL,
    asset_kind varchar(16) NOT NULL CHECK (asset_kind IN ('installer', 'updater')),
    download_url text NOT NULL,
    file_name varchar(256) NOT NULL,
    file_size bigint NOT NULL DEFAULT 0,
    sha256 varchar(64),
    signature text,
    download_count bigint NOT NULL DEFAULT 0,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE release_asset IS '发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）';
COMMENT ON COLUMN release_asset.release_id IS '所属版本 app_release.id（级联删除）';
COMMENT ON COLUMN release_asset.platform_target IS '平台目标（darwin-aarch64/darwin-x86_64/windows-x86_64/linux-x86_64）';
COMMENT ON COLUMN release_asset.asset_kind IS '包类型 (installer:安装包dmg:blue/updater:热更新包:purple)';
COMMENT ON COLUMN release_asset.download_url IS '七牛 CDN 下载地址（https 直链）';
COMMENT ON COLUMN release_asset.file_name IS '文件名';
COMMENT ON COLUMN release_asset.file_size IS '文件字节数';
COMMENT ON COLUMN release_asset.sha256 IS '文件 sha256（完整性校验）';
COMMENT ON COLUMN release_asset.signature IS 'minisign 签名（仅 updater；Tauri 客户端验签用）';
COMMENT ON COLUMN release_asset.download_count IS '下载计数（经计数重定向端点累加）';
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_asset_target_kind ON release_asset (release_id, platform_target, asset_kind);
CREATE INDEX IF NOT EXISTS idx_release_asset_release ON release_asset (release_id);

-- ========== (3) release_build — CI 构建任务（GitHub Actions 进度） ==========
CREATE TABLE IF NOT EXISTS release_build (
    id bigserial PRIMARY KEY,
    ref varchar(128) NOT NULL,
    channel varchar(16) NOT NULL DEFAULT 'stable' CHECK (channel IN ('stable', 'beta')),
    status varchar(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'building', 'success', 'failed')),
    version varchar(32),
    github_run_id varchar(64),
    github_run_url varchar(512),
    triggered_by varchar(64),
    error_message text,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE release_build IS 'CI 构建任务（GitHub Actions 构建进度追踪）';
COMMENT ON COLUMN release_build.ref IS '构建 ref（branch 或 tag，如 main / v1.2.0）';
COMMENT ON COLUMN release_build.channel IS '目标渠道 (stable:稳定版:green/beta:内测版:orange)';
COMMENT ON COLUMN release_build.status IS '构建状态 (queued:排队中:gray/building:构建中:blue/success:成功:green/failed:失败:red)';
COMMENT ON COLUMN release_build.version IS '产出版本号（构建成功后回填）';
COMMENT ON COLUMN release_build.github_run_id IS 'GitHub Actions run id';
COMMENT ON COLUMN release_build.github_run_url IS 'GitHub Actions run 页面链接';
COMMENT ON COLUMN release_build.triggered_by IS '触发者（管理员用户名/hasn_id）';
COMMENT ON COLUMN release_build.error_message IS '失败原因（status=failed 时）';
CREATE INDEX IF NOT EXISTS idx_release_build_status ON release_build (status, created_time);
