-- =====================================================
-- hasn_release · 无头 hasn-node 镜像发布目标（H8）
-- 契约：docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md §1 / §7
--
-- 只加不改：桌面端既有 platform_target（darwin-*/windows-*/linux-x86_64）与
-- asset_kind（installer/updater）的行为完全不变，本迁移只放宽约束、加一列。
--
--   1) app_release 增 min_cloud_contract_version：主后端发布不兼容变更前，
--      先确认在线云端节点已滚动到该契约版本以上。
--   2) release_asset 的 asset_kind 增加 'image'：无头镜像不是可下载文件，
--      download_url 存 registry ref、sha256 存镜像 digest。
--   3) sha256 列宽 64 → 128：docker digest 形如 `sha256:<64hex>` 共 71 字符，
--      原宽度装不下（桌面端存纯 hex，不受影响）。
-- =====================================================
SET search_path TO hasn_release, public;

ALTER TABLE app_release ADD COLUMN IF NOT EXISTS min_cloud_contract_version varchar(32);
COMMENT ON COLUMN app_release.min_cloud_contract_version IS '本版要求的最低云端契约版本（无头镜像滚动更新门槛，NULL=不设门槛）';

ALTER TABLE release_asset DROP CONSTRAINT IF EXISTS release_asset_asset_kind_check;
ALTER TABLE release_asset ADD CONSTRAINT release_asset_asset_kind_check
    CHECK (asset_kind IN ('installer', 'updater', 'image'));
COMMENT ON COLUMN release_asset.asset_kind IS '包类型 (installer:安装包dmg:blue/updater:热更新包:purple/image:容器镜像:cyan)';

ALTER TABLE release_asset ALTER COLUMN sha256 TYPE varchar(128);
COMMENT ON COLUMN release_asset.sha256 IS '文件 sha256；asset_kind=image 时存镜像 digest（sha256:<64hex>）';

COMMENT ON COLUMN release_asset.platform_target IS '平台目标（darwin-aarch64/darwin-x86_64/windows-x86_64/linux-x86_64/headless-linux-amd64/headless-linux-arm64）';
COMMENT ON COLUMN release_asset.download_url IS '七牛 CDN 下载地址（https 直链）；asset_kind=image 时存私有 registry ref';
