-- 分身模板归类合并（一类应用一模板，doc31）：清理已折叠退役的 7 个 agent_template 存量行。
-- 这些模板目录已从 hub 删除（合并进锚点模板），但 github_app_sync 只 upsert、不删除，
-- 故存量行会残留在 marketplace_template（webui 模板列表照样显示已退役模板）。此迁移显式清理。
--
--   退役 → 折叠去向：
--     quant-trader        → analyst（金融理财专家）
--     finance             → analyst（金融理财专家）
--     designsystem-expert → designer（设计专家）
--     media-creator       → content-operator（内容运营官）
--     office              → assistant（全能助理）
--     side-hustle         → startup-advisor（创业军师）
--     researcher          → data-analyst（数据分析专家）
--
-- 幂等：DELETE 不存在的行是 no-op（本地无 quant-trader 行，prod 可能有——覆盖全 7 个以两端通用）。
-- 无 CASCADE，先删 version 子行再删 template 主行。
-- 安全：这些退役模板中仅 designsystem-expert 曾带 builtin_key；reconcile_builtin_agents 是 INSERT-only，
-- 删模板行不影响已存在的分身（存量分身按 builtin_agent_key 各自独立，不随模板行消失）。

DELETE FROM hasn_marketplace.marketplace_template_version
WHERE template_id IN (
    'huanxing/agent/quant-trader',
    'huanxing/agent/finance',
    'huanxing/agent/designsystem-expert',
    'huanxing/agent/media-creator',
    'huanxing/agent/office',
    'huanxing/agent/side-hustle',
    'huanxing/agent/researcher'
);

DELETE FROM hasn_marketplace.marketplace_template
WHERE template_id IN (
    'huanxing/agent/quant-trader',
    'huanxing/agent/finance',
    'huanxing/agent/designsystem-expert',
    'huanxing/agent/media-creator',
    'huanxing/agent/office',
    'huanxing/agent/side-hustle',
    'huanxing/agent/researcher'
);
