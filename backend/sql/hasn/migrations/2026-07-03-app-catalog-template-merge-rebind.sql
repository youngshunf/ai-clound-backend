-- 分身模板归类合并（一类应用一模板，doc31）：catalog 行改绑「默认承接分身类型」到合并后的锚点模板。
-- 原则：一类应用共用一个分身模板；每个应用的工作会话业务提示词（work_session_system_prompt）按应用区分、保留不动。
--
--   ① quant（量化）：quant_trader「量化交易官」折叠进 analyst「金融理财专家」。
--      finance + quant 现同用 analyst 模板；两者工作会话提示词各自保留（finance 投研免责 / quant 回测专属）。
--   ② designsystem（设计系统）：designsystem_expert 折叠进 designer「设计专家」。
--      design + designsystem 现同用 designer 模板；两者工作会话提示词各自保留（矢量出图 / token 契约）。
--
-- 出厂源 _CATALOG_AGENT_DEFAULTS 已同步改为 analyst / designer（app_catalog_service.py）；本迁移刷存量 catalog 行。
-- work_session_system_prompt 不动（合并只换承接模板，不改每应用的执行提示词）。
-- 幂等 + 保护运营改动：仅在当前仍是旧值时改（运营若已自定义则不覆盖）。

UPDATE hasn_app_catalog
SET default_agent_type = 'analyst'
WHERE app_id = 'quant' AND default_agent_type = 'quant_trader';

UPDATE hasn_app_catalog
SET default_agent_type = 'designer'
WHERE app_id = 'designsystem' AND default_agent_type = 'content_operator';
