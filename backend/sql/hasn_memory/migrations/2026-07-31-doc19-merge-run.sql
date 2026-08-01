-- =====================================================
-- doc19 S3 · merge_run（合并轮次）
-- =====================================================
-- 设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md §5.5 / §5.6
--
-- 主脑分身在自己设备上跑完一轮合并后，把整轮结果提交云端合并闸（owner advisory lock +
-- owner_memory.version CAS）。本表登记每一轮的提交者、基线版本、裁决计数与结果摘要：
--   · 支撑 §5.5「主人在记忆页能看到上次整理于 X、主脑在 <设备>、当前离线」；
--   · 支撑 §5.6 拒绝路径的可解释性——status='rejected' 必带 reject_reason（非当前主脑 /
--     基线版本不匹配），主脑下轮重跑，不静默停摆。
-- run_id 同时是 semantic_fact.merge_verdict_run 指向的合并轮次（该列组不建外键，§3.2）。
--
-- 幂等：CREATE TABLE / CREATE INDEX IF NOT EXISTS，可重复执行。
-- =====================================================

SET search_path TO hasn_memory, public;

CREATE TABLE IF NOT EXISTS merge_run (
    run_id varchar(40) NOT NULL,
    owner_id varchar(40) NOT NULL,
    submitted_node_id varchar(64) NOT NULL,
    submitted_agent_id varchar(40) NOT NULL,
    base_owner_memory_version integer NOT NULL DEFAULT 0,
    status varchar(16) NOT NULL DEFAULT 'applied',
    reject_reason varchar(64),
    facts_judged integer NOT NULL DEFAULT 0,
    facts_merged integer NOT NULL DEFAULT 0,
    facts_disputed integer NOT NULL DEFAULT 0,
    summary text,
    started_time timestamptz(6) NOT NULL DEFAULT now(),
    finished_time timestamptz(6),
    created_time timestamptz(6) NOT NULL DEFAULT now(),
    updated_time timestamptz(6),
    CONSTRAINT pk_merge_run PRIMARY KEY (run_id),
    CONSTRAINT ck_merge_run_status CHECK (status IN ('applied', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_merge_run_owner_finished
    ON merge_run (owner_id, finished_time DESC);

COMMENT ON TABLE merge_run IS 'HASN 记忆系统 - 合并轮次（主脑提交、云端合并闸裁定）';
COMMENT ON COLUMN merge_run.run_id IS '合并轮次 ID（主键；semantic_fact.merge_verdict_run 指向它）';
COMMENT ON COLUMN merge_run.owner_id IS '主人 hasn_id（一轮合并只针对一个主人）';
COMMENT ON COLUMN merge_run.submitted_node_id IS '提交节点 node_id（主脑所在设备）';
COMMENT ON COLUMN merge_run.submitted_agent_id IS '提交分身 hasn_id（主脑分身）';
COMMENT ON COLUMN merge_run.base_owner_memory_version IS '提交声明的基线 owner_memory.version（合并闸 CAS 依据）';
COMMENT ON COLUMN merge_run.status IS '轮次结果 (applied:已应用:green/rejected:已拒绝:red)';
COMMENT ON COLUMN merge_run.reject_reason IS '拒绝原因（not_master_brain / version_conflict 等；status=rejected 时必填）';
COMMENT ON COLUMN merge_run.facts_judged IS '本轮读入裁决的活跃事实数';
COMMENT ON COLUMN merge_run.facts_merged IS '本轮标 merged_into 的事实数';
COMMENT ON COLUMN merge_run.facts_disputed IS '本轮标 disputed（待主人确认）的事实数';
COMMENT ON COLUMN merge_run.summary IS '主脑用人话写的结果摘要（面向主人，记忆页可见）';
COMMENT ON COLUMN merge_run.started_time IS '本轮开始时间';
COMMENT ON COLUMN merge_run.finished_time IS '本轮结束时间（含被拒）';
COMMENT ON COLUMN merge_run.created_time IS '创建时间';
COMMENT ON COLUMN merge_run.updated_time IS '更新时间';
