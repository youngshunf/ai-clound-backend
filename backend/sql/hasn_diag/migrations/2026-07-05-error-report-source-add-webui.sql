-- 2026-07-05：error_report.source 合法值追加 'webui'（WHITESCREEN Part2 云端收口）。
--
-- Why：daemon 侧 doc21 源③（webui 前端错误上报）已落地——webui 崩溃经
-- `POST /api/v1/diag/webui/errors` 进 daemon diag outbox、以 source='webui' 批量上云；
-- 但云端 `DiagErrorEvent.source` Literal 与本表 CHECK 约束都只收 daemon/hermes/runtime，
-- 含 webui 事件的整批被 422 拒收 → 该批 daemon/hermes 事件一并卡死（毒丸批），
-- 节点错误遥测自首条 webui 事件起全线停摆（2026-07-05 好友节点实测：10:08 后零上报）。
-- 本迁移与 schema/error_sync.py Literal 扩展同刀落地。
ALTER TABLE hasn_diag.error_report DROP CONSTRAINT IF EXISTS error_report_source_check;
ALTER TABLE hasn_diag.error_report ADD CONSTRAINT error_report_source_check
    CHECK (source IN ('daemon', 'hermes', 'runtime', 'webui'));
COMMENT ON COLUMN hasn_diag.error_report.source IS '来源 (daemon:daemon:blue/hermes:本地hermes:cyan/runtime:云端runtime:purple/webui:前端webui:orange)';
