-- AI-Native 应用入口路由统一到 /apps/<id>：把全部 11 个内置应用的
-- hasn_app_catalog.entry_route（"客户端原生路由"）改写为 /apps/<id>。
--
-- 背景：entry_route 此前前缀混乱——6 个挂 /workbench/apps/*（knowledge/plan/deck/
--   designsystem/copilot/film），5 个是裸顶层路由（/tasks /community /creator /publish
--   /growth）。WebUI 启动器据 entry_route 用 <Link to={entry_route}> 寻址，本次把客户端
--   路由/页面统一到 /apps/<id> 并去掉 workbench 命名（详见父仓计划 ai-native-apps）。
-- 为何需要本迁移：app_catalog_service.ensure_catalog_seeded 是 INSERT-only（仅补缺失
--   app_id，已存在行原样保留），故改 manifest 不改存量库行——必须用迁移覆盖式改写。
-- 幂等：按 app_id 直接 UPDATE（出厂数据，覆盖式无条件），runner 只跑一次；重跑同值无副作用。
-- app_id 对照：tasks 应用的 app_id 是 'hasn_task'。

UPDATE hasn_app_catalog SET entry_route = '/apps/knowledge'    WHERE app_id = 'knowledge';
UPDATE hasn_app_catalog SET entry_route = '/apps/community'    WHERE app_id = 'community';
UPDATE hasn_app_catalog SET entry_route = '/apps/deck'         WHERE app_id = 'deck';
UPDATE hasn_app_catalog SET entry_route = '/apps/tasks'        WHERE app_id = 'hasn_task';
UPDATE hasn_app_catalog SET entry_route = '/apps/publish'      WHERE app_id = 'publish';
UPDATE hasn_app_catalog SET entry_route = '/apps/growth'       WHERE app_id = 'growth';
UPDATE hasn_app_catalog SET entry_route = '/apps/creator'      WHERE app_id = 'creator';
UPDATE hasn_app_catalog SET entry_route = '/apps/designsystem' WHERE app_id = 'designsystem';
UPDATE hasn_app_catalog SET entry_route = '/apps/film'         WHERE app_id = 'film';
UPDATE hasn_app_catalog SET entry_route = '/apps/copilot'      WHERE app_id = 'copilot';
UPDATE hasn_app_catalog SET entry_route = '/apps/plan'         WHERE app_id = 'plan';
