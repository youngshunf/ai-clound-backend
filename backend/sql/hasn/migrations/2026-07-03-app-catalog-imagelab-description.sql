-- imagelab（图坊）catalog 描述文案修订：去掉「，产物默认本地、点分享才上云」这句（福仔 2026-07-03）。
--
-- 背景：2026-07-02-app-catalog-imagelab.sql 的 INSERT 是 ON CONFLICT (app_id) DO NOTHING，
--   且其 ②③④ 步只回填 config_json / default_agent_type / icon，**从不 UPDATE description**——
--   故存量 dev/prod 的 imagelab 行仍带旧文案末句。出厂源（manifest.build_imagelab_app 的 App.description）
--   已同步删句，新部署经 ensure_catalog_seeded 即得本值；本迁移显式修正**存量行**。
-- 幂等：无条件 UPDATE 目标文案（同 icon 回填范式）；再跑一次为等值写入，安全。
--   本迁移文件名日期晚于 2026-07-02，runner 顺序执行，故新环境 INSERT 旧文案后本步立即纠正为新文案。

UPDATE hasn_app_catalog
SET description = '自研本地图像处理引擎——去背景/裁剪/调色/滤镜/水印/拼图/压缩/动画/超分/局部消除，分身用「处理配方」编排批量处理。'
WHERE app_id = 'imagelab';
