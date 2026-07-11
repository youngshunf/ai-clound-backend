-- 一次性清理：抹除 diag「自反馈环」放大出来的噪音 error_report 行
-- ============================================================================
-- 背景（根因）：
--   daemon 的 diag 推送失败告警本身（huanxing transport 打的
--   `backend returned client-error status (4xx)`，结构化字段 operation="diag_errors_sync"）
--   命中了 diag 捕获 Layer 的 WARN 白名单（target 前缀 hasn_node::backend）→ 被再次捕获
--   → 再次入 outbox → 再次上推云端 → 再次被限频 → 再次告警……形成自我放大环。
--   结果：117 生产库两个用户几天累计 ~3.8 万条 error_report，绝大多数是这条自指噪音。
--
--   反馈环已在 hasn-node 侧根治（diag/layer.rs::is_self_referential 在 admit 前丢弃
--   operation=diag_errors_sync / target=hasn_node::diag* 的事件，commit f9c6ba1c），
--   本脚本只负责清理**存量历史行**。
--
-- 噪音判据（精确·只认 diag 自身，绝不误伤业务 4xx）：
--   `context_json->>'operation' = 'diag_errors_sync'`——operation 是唯一区分符。
--   （泛化的「backend returned client-error status (4xx)」消息对所有 4xx 后端调用都会打，
--    绝不能只按 message 删，否则会连累真正的 knowledge_search 404 / 409 冲突等业务告警。）
--   兜底再补两条：context_json 文本里出现 diag_errors_sync（operation 万一嵌在别处）、
--   以及 diag outbox 超上限丢弃汇报（万一有历史行漏进来）。
--
-- 用法：
--   1) 先只跑「第一段·体检（只读）」，人工核对噪音占比与样本，确认无误伤业务行。
--   2) 再跑「第二段·执行清理（事务）」——删噪音 report + 重算/清理受影响 issue。
--      整段包在一个事务里，出错自动回滚；核对 RAISE NOTICE 的删除条数后再 COMMIT。
--   执行前建议先 pg_dump 备份 hasn_diag schema（见 wiki 生产部署 runbook）。
-- ============================================================================


-- ────────────────────────────────────────────────────────────────────────────
-- 第一段 · 体检（只读，不改任何数据）
-- ────────────────────────────────────────────────────────────────────────────

-- 1.1 总量 vs 噪音量占比
SELECT
    count(*)                                                        AS total_reports,
    count(*) FILTER (WHERE context_json->>'operation' = 'diag_errors_sync') AS noise_by_operation,
    count(*) FILTER (WHERE context_json::text LIKE '%diag_errors_sync%')    AS noise_by_context_text,
    count(*) FILTER (WHERE message LIKE '%diag outbox%' AND message LIKE '%丢弃%') AS noise_outbox_drop
FROM hasn_diag.error_report;

-- 1.2 噪音行样本（确认确实是 diag 自指、不是业务错误）
SELECT id, source, severity, fingerprint, message,
       context_json->>'operation' AS operation, occurred_at
FROM hasn_diag.error_report
WHERE context_json->>'operation' = 'diag_errors_sync'
   OR context_json::text LIKE '%diag_errors_sync%'
   OR (message LIKE '%diag outbox%' AND message LIKE '%丢弃%')
ORDER BY occurred_at DESC
LIMIT 20;

-- 1.3 受影响的 error_issue（按 fingerprint 归类）——看这些 issue 是否已被人工分诊
--     （status != 'open' 表示有人处理过，清理时会保留 issue 只重算计数，不删）
SELECT ei.fingerprint, ei.title, ei.status, ei.occurrence_count,
       count(er.id) AS noise_report_rows
FROM hasn_diag.error_issue ei
JOIN hasn_diag.error_report er ON er.fingerprint = ei.fingerprint
WHERE er.context_json->>'operation' = 'diag_errors_sync'
   OR er.context_json::text LIKE '%diag_errors_sync%'
   OR (er.message LIKE '%diag outbox%' AND er.message LIKE '%丢弃%')
GROUP BY ei.fingerprint, ei.title, ei.status, ei.occurrence_count
ORDER BY noise_report_rows DESC;


-- ────────────────────────────────────────────────────────────────────────────
-- 第二段 · 执行清理（事务，核对 NOTICE 后 COMMIT）
-- ────────────────────────────────────────────────────────────────────────────
-- 核对第一段结果无误后，去掉下面的注释块整体执行。整段一个事务，出错自动回滚。

/*
BEGIN;

-- 2.1 记下噪音 fingerprint 集合（供后面 issue 重算/清理用）
CREATE TEMP TABLE _diag_noise_fp ON COMMIT DROP AS
SELECT DISTINCT fingerprint
FROM hasn_diag.error_report
WHERE context_json->>'operation' = 'diag_errors_sync'
   OR context_json::text LIKE '%diag_errors_sync%'
   OR (message LIKE '%diag outbox%' AND message LIKE '%丢弃%');

-- 2.2 删噪音 occurrence 行
WITH deleted AS (
    DELETE FROM hasn_diag.error_report
    WHERE context_json->>'operation' = 'diag_errors_sync'
       OR context_json::text LIKE '%diag_errors_sync%'
       OR (message LIKE '%diag outbox%' AND message LIKE '%丢弃%')
    RETURNING id
)
SELECT count(*) AS deleted_report_rows FROM deleted \gset
DO $$ BEGIN RAISE NOTICE '已删除噪音 error_report 行数：%', :'deleted_report_rows'; END $$;

-- 2.3 重算受影响 issue 的累计计数（occurrence_count = Σ(1 + suppressed_count) 剩余行）
--     issue.occurrence_count 口径含 suppressed_count（见列注释）。
UPDATE hasn_diag.error_issue ei
SET occurrence_count = COALESCE(agg.cnt, 0),
    first_seen_at    = COALESCE(agg.first_seen, ei.first_seen_at),
    last_seen_at     = COALESCE(agg.last_seen, ei.last_seen_at)
FROM (
    SELECT er.fingerprint,
           sum(1 + greatest(er.suppressed_count, 0)) AS cnt,
           min(er.occurred_at)                        AS first_seen,
           max(er.occurred_at)                        AS last_seen
    FROM hasn_diag.error_report er
    GROUP BY er.fingerprint
) agg
WHERE ei.fingerprint = agg.fingerprint
  AND ei.fingerprint IN (SELECT fingerprint FROM _diag_noise_fp);

-- 2.4 清理彻底空掉的纯噪音 issue：受影响 fingerprint 里，已无任何剩余 report、
--     且 status='open'（从未被人工分诊/结案）→ 说明是自指噪音派生的 issue，删掉。
--     status != 'open' 的即便清零也保留（尊重人工处理痕迹）。
WITH orphaned AS (
    DELETE FROM hasn_diag.error_issue ei
    WHERE ei.fingerprint IN (SELECT fingerprint FROM _diag_noise_fp)
      AND ei.status = 'open'
      AND NOT EXISTS (
          SELECT 1 FROM hasn_diag.error_report er WHERE er.fingerprint = ei.fingerprint
      )
    RETURNING id
)
SELECT count(*) AS deleted_issue_rows FROM orphaned \gset
DO $$ BEGIN RAISE NOTICE '已删除空掉的纯噪音 error_issue 行数：%', :'deleted_issue_rows'; END $$;

-- 2.5 复核：清理后总量 + 残留噪音（应为 0）
SELECT count(*) AS reports_after,
       count(*) FILTER (WHERE context_json->>'operation' = 'diag_errors_sync') AS noise_remaining
FROM hasn_diag.error_report;

-- 核对以上 NOTICE / 复核结果无误后：
COMMIT;
-- 若有异常：ROLLBACK;
*/
