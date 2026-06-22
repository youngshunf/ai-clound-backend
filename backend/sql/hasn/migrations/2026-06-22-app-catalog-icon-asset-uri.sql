-- 应用中心图标改用公共桶图片：把 10 个内置应用的 hasn_app_catalog.icon_asset_uri 回填为
-- 公共桶（hasn-pub）CDN 直读 URL。
--
-- 背景：工作台「应用中心」卡片图标走 icon_asset_uri（图片优先于 icon token，见 webui
--   WorkbenchAppCard.AppIconVisual）。此前 brand-* token 仅新构建 webui 才认识，已部署桌面端
--   不渲染、落单色兜底图标（福仔反馈「图标没有任何变化」）。改走 deck 同款图片路：图片 URL
--   写进 icon_asset_uri，已部署构建即可直接渲染、无需重打包。
-- 资产来源：兄弟仓 hasn-node/webui/public/app-icons/{app_id}.svg，经
--   backend/scripts/upload_app_icons.py 上传到公共桶 huanxing/app-icons/{app_id}.svg。
--   dev 与生产共用同一七牛公共桶 hasn-pub，故下方固定 URL 两端通用、本迁移可直接回填两端 DB。
-- deck 不在内（其 icon_asset_uri 已有一张线上图标，保留不动）。
-- 幂等：按 app_id 直接 UPDATE 设定品牌图标 URL（出厂品牌资产，覆盖式无条件），runner 只跑一次。

UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/knowledge.svg' WHERE app_id = 'knowledge';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/community.svg' WHERE app_id = 'community';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/publish.svg' WHERE app_id = 'publish';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/growth.svg' WHERE app_id = 'growth';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/creator.svg' WHERE app_id = 'creator';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/designsystem.svg' WHERE app_id = 'designsystem';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/film.svg' WHERE app_id = 'film';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/copilot.svg' WHERE app_id = 'copilot';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/plan.svg' WHERE app_id = 'plan';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/hasn_task.svg' WHERE app_id = 'hasn_task';
