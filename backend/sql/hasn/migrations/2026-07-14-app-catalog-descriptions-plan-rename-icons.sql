-- 应用中心产品化打磨（福仔 2026-07-14）：hasn_app_catalog 的「后段应用描述产品化 + 规划改名 + 两个缺失图标补 CDN」三件事。
-- 背景：catalog 是工作台「应用中心」展示的 DB 权威（C2）；ensure_catalog_seeded 仅 INSERT 不回写已存在行，
--   故存量 dev/prod 需此迁移把新出厂值刷进存量库。出厂源（各 manifest.py + registry）已同步同值，新部署 seed 即得新值；
--   本迁移只修存量。幂等：按 app_id 无条件 UPDATE 到目标值，runner 只跑一次、重复跑也安全。
--
-- 三段语义：
--   ① description：把「后段应用」（量化交易/视频引擎/矢量设计/图坊/短视频合成）的技术味文案（cloud-brokered/
--      hasn.design.*/sidecar/自研引擎…）重铸为用户向、以「交给分身干什么」为主语的产品化文案，
--      与前段应用（知识库/社区/演示文稿…）口径一致。
--   ② name：规划 → 日程与规划（更贴合「目标+计划+待办+日程」的完整心智，与图标/入口一致）。
--   ③ icon_asset_uri：computer_use（桌面控制）与 finance（金融数据）此前 catalog 无/引用了源目录缺失的 svg
--      → 已部署桌面端落单色兜底图标（福仔反馈「桌面控制图标缺失」）。现补齐两张品牌 svg 上传公共桶
--      （backend.scripts.upload_app_icons 扫描源目录 hasn-node/webui/public/app-icons/*.svg 自动上传），
--      此处把 icon_asset_uri 回填为固定 CDN 直读 URL（dev/prod 共用同一七牛公共桶 hasn-pub，两端通用）。

-- ① 后段应用描述产品化（与各 manifest.py App.description 同值）。
UPDATE hasn_app_catalog SET description = '把你的交易想法交给分身写成策略、跑历史回测、出绩效报告、反复打磨——回测阶段不碰真钱，验证成熟再上实盘。'
    WHERE app_id = 'quant';

UPDATE hasn_app_catalog SET description = '一句创意交给分身出成片——脚本、分镜、配音、合成一条龙走完，出片、改片、管素材都在一处。'
    WHERE app_id = 'studio';

UPDATE hasn_app_catalog SET description = '和分身一起在画布上做设计——海报、UI 稿、插画、图形、Logo，你说想法它实时出图，源文件随时可改。'
    WHERE app_id = 'design';

UPDATE hasn_app_catalog SET description = '把图片处理的杂活交给分身——抠图换背景、裁剪调色、加水印、拼图压缩、做动画、超分修复，还能存成配方批量跑。'
    WHERE app_id = 'imagelab';

UPDATE hasn_app_catalog SET description = '一个主题就能出短视频——分身替你写文案、配音、加字幕、配素材、自动拼成片，口播、带货、资讯批量产出。'
    WHERE app_id = 'reel';

-- ② 规划 → 日程与规划（与 hasn_plan/manifest.py App.name 同值）。
UPDATE hasn_app_catalog SET name = '日程与规划' WHERE app_id = 'plan';

-- ③ 补两个缺失图标的 CDN 直读 URL（源 svg 已随本次一并加入 app-icons/ 并上传公共桶）。
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/computer_use.svg' WHERE app_id = 'computer_use';
UPDATE hasn_app_catalog SET icon_asset_uri = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons/finance.svg' WHERE app_id = 'finance';
