-- 项目管理（project）补品牌图标：hasn_app_catalog.icon_asset_uri 回填 CDN 直读 URL。
-- 背景：project 行早已 seed 进 catalog（icon='brand-project'），但 icon_asset_uri 一直为 NULL
--   → 已部署/打包客户端图标优先读 icon_asset_uri，缺省落单色兜底气泡（应用中心卡片无品牌图标）。
--   现补齐 hasn-node/webui/public/app-icons/project.svg（青柠渐变 + folder-kanban 白色字形），
--   经 backend.scripts.upload_app_icons 上传公共桶 hasn-pub（dev/prod 共用，两端通用）后回填。
-- 幂等：按 app_id 无条件 UPDATE 到目标值，重复执行安全；新部署 seed 由
--   app_catalog_service._CATALOG_SEED_ICON_ASSET_URI 同值保证。
UPDATE hasn_app_catalog
    SET icon = 'brand-project',
        icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/project.svg'
    WHERE app_id = 'project';
