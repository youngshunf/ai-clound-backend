-- 项目管理（project）描述文案产品化：去掉「各应用」「挂到它下面」与应用名斜杠枚举等内部腔，
-- 对齐其他应用的用户向句式（价值开场——内容物——分身视角收尾）。
-- 背景：catalog 是应用中心展示的 DB 权威（C2），ensure_catalog_seeded 仅 INSERT 不回写存量行；
--   出厂源 hasn_project/manifest.py App.description 已同步同值（新部署 seed 即得新值），本迁移只刷存量库。
-- 幂等：按 app_id 无条件 UPDATE 到目标值，重复执行安全。
UPDATE hasn_app_catalog
    SET description = '一件事，一个「项目」——相关的资料、图片、网页和成果都归到一处，分身的工作进展一页看全。'
    WHERE app_id = 'project';
