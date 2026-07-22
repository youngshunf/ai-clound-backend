-- 一次性清理：抹除纯 ERROR 口径切换前积压的 severity='warn' 存量 error_report 行
-- ============================================================================
-- 背景（根因）：
--   diag 采集历史上默认 min_severity=warn + 一份 WARN 白名单，实测上报里 98.7% 是
--   **可恢复噪音**（SIGTERM 关停、MCP 重试、keepalive 重连、后端 4xx 客户端错误……），
--   与「warn vs error 铁律」判据相悖——真·终局故障才该上报。单个本机测试客户端几天
--   就堆出 ~13 万行 error_report，其中绝大多数是 severity='warn'。
--
--   采集口径已收敛为**纯 ERROR/CRITICAL**（hasn-node commit 181ef43a：
--   default_diag_min_severity → error、退役 WARN 白名单默认），此后不再产生 warn 行。
--   本脚本只负责清理**存量历史 warn 行**。
--
-- 噪音判据（精确·只认 severity='warn'）：
--   `severity = 'warn'`——列 CHECK 约束为 IN ('critical','error','warn')，warn 即全部
--   可恢复级噪音。critical / error 一律保留（真·终局故障，长期证据）。
--
--   fingerprint 不含 severity（= sha256(source|error_class/消息|模块级位置)），故同一
--   fingerprint 理论上可同时有 warn 与 error 行（同消息不同级别）。本脚本只删 warn
--   occurrence 行，保留 error 行；受影响 issue 按剩余行重算计数，**只删「清零且从未被
--   人工分诊(status='open')」的纯噪音 issue**，尊重人工处理痕迹。
--
-- 用法：
--   1) 先只跑「第一段·体检（只读）」，人工核对 warn 占比与样本，确认无误伤 error/critical。
--   2) 再跑「第二段·执行清理（事务）」——删 warn report + 重算/清理受影响 issue。
--      整段包在一个事务里，出错自动回滚；核对 RAISE NOTICE 的删除条数后再 COMMIT。
--   执行前建议先 pg_dump 备份 hasn_diag schema（见生产部署 runbook）。
--   本地已跑过前序清理 2026-07-10-purge-diag-feedback-loop-noise.sql（diag 自反馈环噪音），
--   本脚本是更宽的 warn 存量清理，二者叠加幂等（此处按 severity 删，diag_errors_sync 噪音本
--   也是 warn，会一并被清）。
-- ============================================================================


-- ────────────────────────────────────────────────────────────────────────────
-- 第一段 · 体检（只读，不改任何数据）
-- ────────────────────────────────────────────────────────────────────────────

-- 1.1 总量 vs warn 噪音量占比（error/critical 应被完整保留）
SELECT
    count(*)                                              AS total_reports,
    count(*) FILTER (WHERE severity = 'warn')             AS warn_noise,
    count(*) FILTER (WHERE severity = 'error')            AS error_kept,
    count(*) FILTER (WHERE severity = 'critical')         AS critical_kept,
    round(100.0 * count(*) FILTER (WHERE severity = 'warn') / nullif(count(*), 0), 1)
                                                          AS warn_pct
FROM hasn_diag.error_report;

-- 1.2 warn 噪音 Top fingerprint（确认确实是可恢复噪音，非误判为 warn 的真故障）
SELECT fingerprint,
       min(source)                 AS source,
       min(location)               AS sample_location,
       max(message)                AS sample_message,
       count(*)                    AS warn_rows
FROM hasn_diag.error_report
WHERE severity = 'warn'
GROUP BY fingerprint
ORDER BY warn_rows DESC
LIMIT 25;

-- 1.3 受影响的 error_issue——区分「纯 warn（会清零）」与「混合(含 error/critical，保留计数)」
SELECT ei.fingerprint, ei.title, ei.severity, ei.status, ei.occurrence_count,
       count(er.id) FILTER (WHERE er.severity = 'warn')      AS warn_rows,
       count(er.id) FILTER (WHERE er.severity <> 'warn')     AS non_warn_rows
FROM hasn_diag.error_issue ei
JOIN hasn_diag.error_report er ON er.fingerprint = ei.fingerprint
GROUP BY ei.fingerprint, ei.title, ei.severity, ei.status, ei.occurrence_count
HAVING count(er.id) FILTER (WHERE er.severity = 'warn') > 0
ORDER BY warn_rows DESC
LIMIT 40;


-- ────────────────────────────────────────────────────────────────────────────
-- 第二段 · 执行清理（事务，核对 NOTICE 后 COMMIT）
-- ────────────────────────────────────────────────────────────────────────────
-- 核对第一段结果无误后，去掉下面的注释块整体执行。整段一个事务，出错自动回滚。

/*
BEGIN;

-- 2.1 记下受影响 fingerprint 集合（含 warn 行的 issue，供后面重算/清理用）
CREATE TEMP TABLE _diag_warn_fp ON COMMIT DROP AS
SELECT DISTINCT fingerprint
FROM hasn_diag.error_report
WHERE severity = 'warn';

-- 2.2 删 warn occurrence 行（critical/error 一行不动）
WITH deleted AS (
    DELETE FROM hasn_diag.error_report
    WHERE severity = 'warn'
    RETURNING id
)
SELECT count(*) AS deleted_report_rows FROM deleted \gset
DO $$ BEGIN RAISE NOTICE '已删除 warn 存量 error_report 行数：%', :'deleted_report_rows'; END $$;

-- 2.3 重算受影响 issue 的累计计数（occurrence_count = Σ(1 + suppressed_count) 剩余行）
--     混合 issue（尚有 error/critical 行）→ 计数收敛到非 warn 行；纯 warn issue → 归 0。
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
  AND ei.fingerprint IN (SELECT fingerprint FROM _diag_warn_fp);

-- 2.4 清理彻底空掉的纯噪音 issue：受影响 fingerprint 里，已无任何剩余 report、
--     且 status='open'（从未被人工分诊/结案）→ 纯 warn 噪音派生的 issue，删掉。
--     status != 'open' 的即便清零也保留（尊重人工处理痕迹）。
WITH orphaned AS (
    DELETE FROM hasn_diag.error_issue ei
    WHERE ei.fingerprint IN (SELECT fingerprint FROM _diag_warn_fp)
      AND ei.status = 'open'
      AND NOT EXISTS (
          SELECT 1 FROM hasn_diag.error_report er WHERE er.fingerprint = ei.fingerprint
      )
    RETURNING fingerprint
)
SELECT count(*) AS deleted_issue_rows FROM orphaned \gset
DO $$ BEGIN RAISE NOTICE '已删除空掉的纯噪音 error_issue 行数：%', :'deleted_issue_rows'; END $$;

-- 2.5 清理被删 issue 的孤儿状态流水/已读游标（按 fingerprint 软关联，无级联，须手动清）。
WITH del_ev AS (
    DELETE FROM hasn_diag.error_issue_event ev
    WHERE ev.fingerprint IN (SELECT fingerprint FROM _diag_warn_fp)
      AND NOT EXISTS (
          SELECT 1 FROM hasn_diag.error_issue ei WHERE ei.fingerprint = ev.fingerprint
      )
    RETURNING id
)
SELECT count(*) AS deleted_event_rows FROM del_ev \gset
DO $$ BEGIN RAISE NOTICE '已删除孤儿 error_issue_event 行数：%', :'deleted_event_rows'; END $$;

WITH del_seen AS (
    DELETE FROM hasn_diag.error_issue_seen se
    WHERE se.fingerprint IN (SELECT fingerprint FROM _diag_warn_fp)
      AND NOT EXISTS (
          SELECT 1 FROM hasn_diag.error_issue ei WHERE ei.fingerprint = se.fingerprint
      )
    RETURNING id
)
SELECT count(*) AS deleted_seen_rows FROM del_seen \gset
DO $$ BEGIN RAISE NOTICE '已删除孤儿 error_issue_seen 行数：%', :'deleted_seen_rows'; END $$;

-- 2.6 复核：清理后总量 + 残留 warn（应为 0）
SELECT count(*)                                    AS reports_after,
       count(*) FILTER (WHERE severity = 'warn')   AS warn_remaining
FROM hasn_diag.error_report;

-- 核对以上 NOTICE / 复核结果无误后：
COMMIT;
-- 若有异常：ROLLBACK;
*/
