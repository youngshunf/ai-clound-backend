-- FILMCFG-1：hasn_app_catalog 增加 config_json（应用专属平台级配置容器）。
--
-- 每个应用一行、自治其配置（如 film 的 5 类模型 failover + 引擎包 manifest 内联）；
-- 管理端直接编辑该 JSON，platform-config 端点（get_effective_config）聚合各应用 config_json
-- 成 app_configs 拼进下发响应，compute_revision 自动涵盖 → catalog 配置变即 revision 变 →
-- daemon 重拉。取代原 PDC node.film 通道（PlatformFilmDefaults 已退役）。
--
-- 幂等：IF NOT EXISTS，可重复执行。

ALTER TABLE "public"."hasn_app_catalog"
    ADD COLUMN IF NOT EXISTS "config_json" jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN "public"."hasn_app_catalog"."config_json" IS
    '应用专属平台级配置 JSON（每应用自治，如 film 5 类模型 failover + 引擎包 manifest 内联）；管理端直接编辑，platform-config 聚合下发';
