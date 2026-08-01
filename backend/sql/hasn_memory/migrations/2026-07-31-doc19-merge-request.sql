-- =====================================================
-- doc19 S3 · merge_request（主脑离线时的合并待办）
-- =====================================================
-- 设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md §5.5
--
-- 非主脑分身整理完自己那片后可请求合并；请求投递给主脑所在节点。**主脑离线时落云端每
-- owner 待办**——「去重只留最新一条，不堆积」在结构上钉死：owner_id 即主键，重复请求走
-- upsert 覆盖，天然不排队。主脑上线后由 task_scheduler 触发时顺带消化（consumed_time 落时间）；
-- 待办滞留时长计入 §5.5 的「超过阈值未成功合并」提示。
--
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行。
-- =====================================================

SET search_path TO hasn_memory, public;

CREATE TABLE IF NOT EXISTS merge_request (
    owner_id varchar(40) NOT NULL,
    requested_time timestamptz(6) NOT NULL DEFAULT now(),
    requested_by_agent varchar(40) NOT NULL,
    requested_by_node varchar(64) NOT NULL,
    reason varchar(64),
    consumed_time timestamptz(6),
    created_time timestamptz(6) NOT NULL DEFAULT now(),
    updated_time timestamptz(6),
    CONSTRAINT pk_merge_request PRIMARY KEY (owner_id)
);

COMMENT ON TABLE merge_request IS 'HASN 记忆系统 - 合并待办（每主人至多一条，主键去重不堆积）';
COMMENT ON COLUMN merge_request.owner_id IS '主人 hasn_id（主键：每主人至多一条待办，重复请求覆盖）';
COMMENT ON COLUMN merge_request.requested_time IS '最近一次请求时间（滞留时长 = now - 本值）';
COMMENT ON COLUMN merge_request.requested_by_agent IS '发起请求的分身 hasn_id（非主脑分身）';
COMMENT ON COLUMN merge_request.requested_by_node IS '发起请求的节点 node_id';
COMMENT ON COLUMN merge_request.reason IS '请求原因（local_review_done / owner_manual 等）';
COMMENT ON COLUMN merge_request.consumed_time IS '被主脑消化的时间（NULL = 待办仍在）';
COMMENT ON COLUMN merge_request.created_time IS '创建时间';
COMMENT ON COLUMN merge_request.updated_time IS '更新时间';
