-- 彻底删除 Presenton 演示文稿应用（app_id='presentation'）的目录/manifest/权益/工作空间残留行，
-- 并把自研 deck（模块 17）翻成唯一默认演示文稿应用（catalog default_mount=true，对应 install_policy=auto）。
--
-- 背景：早期按 14-AI-Native应用平台/12-Presenton演示文稿应用接入设计.md 接入开源 Presenton
--       作为 embedded_desktop sidecar 应用；团队自研 deck 成熟后彻底下线 Presenton，由 deck 接管。
-- 代码侧（hasn-node sidecar/MCP + 云端 manifest/scope/registry）已删；本迁移清理数据残留。
-- 幂等：DELETE ... WHERE / UPDATE ... WHERE，多次执行安全（无 presentation 行时为 no-op）。
-- 回滚：presentation 行为内置 seed 产物，删后可由旧代码 re-seed 复现（但旧代码已删，不再复现）。

-- 1) 删 Presenton 在四张表里的残留行。
DELETE FROM hasn_app_catalog WHERE app_id = 'presentation';
DELETE FROM hasn_ai_native_app_manifest WHERE app_id = 'presentation';
DELETE FROM hasn_app_entitlement WHERE app_id = 'presentation';
DELETE FROM hasn_workspace_app WHERE app_id = 'presentation';

-- 2) deck 翻为唯一默认演示文稿应用（catalog 无 install_policy 列，default_mount=true 即 install_policy=auto，
--    与代码 build_deck_workbench_app / app_catalog_service seed 对齐）。
UPDATE hasn_app_catalog
SET default_mount = true
WHERE app_id = 'deck';
