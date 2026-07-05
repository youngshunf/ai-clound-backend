-- 错误诊断与可观测性 hasn_diag：4 张表（设计 docs/hasn-node设计文档/21-错误诊断与可观测性/00 §5.2）。
-- 落 schema hasn_diag（ADR-15 应用独立 schema）；PostgreSQL 语法。
--
-- 设计要旨（doc21）：
--   - error_report = 逐条 occurrence（原始事件·(node_id, dedup_key) 幂等去重后入库·TTL 90 天）；
--   - error_issue = 按 fingerprint 聚合的「问题」= 运维处理与状态的单元（长期保留·知识资产）；
--   - error_issue_seen = affected 计数辅助表（防每事件 COUNT DISTINCT 热点；累计口径，report TTL 清理不回缩）；
--   - error_issue_event = 处理动作审计流水（含系统自动重开哨兵 actor 'system:auto-reopen'）。
-- 回归自动重开按状态分号（§5.2）：resolved=版本感知（fixed_in_version）/ skipped=snooze（snooze_until）/ wontfix=不自动重开。
-- 口径注：doc21 DDL 初稿写 owner_id varchar(40)，落地对齐仓内行级隔离标准列名 owner_hasn_id varchar(64)（PLAN-ENT 同口径）。

-- 应用独立 schema（ADR-15）。须先建 schema，否则 SET search_path 静默回落 public（codegen 误建 public）。
CREATE SCHEMA IF NOT EXISTS hasn_diag;
SET search_path TO hasn_diag, public;

-- ========== (1) error_report — 逐条 occurrence（原始事件·去重后入库） ==========
CREATE TABLE IF NOT EXISTS error_report (
    id bigserial PRIMARY KEY,
    node_id varchar(64) NOT NULL,
    owner_hasn_id varchar(64),
    agent_hasn_id varchar(64),
    source varchar(16) NOT NULL CHECK (source IN ('daemon', 'hermes', 'runtime', 'webui')),
    severity varchar(16) NOT NULL CHECK (severity IN ('critical', 'error', 'warn')),
    fingerprint varchar(64) NOT NULL,
    dedup_key varchar(96) NOT NULL,
    error_class varchar(128),
    message text NOT NULL,
    location varchar(256),
    context_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    suppressed_count integer NOT NULL DEFAULT 0,
    app_version varchar(32),
    platform varchar(32),
    occurred_at timestamptz NOT NULL,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE error_report IS '错误事件 occurrence（原始证据流·(node_id,dedup_key) 幂等·TTL 90 天）';
COMMENT ON COLUMN error_report.node_id IS '上报设备 node_id（客户端自报，尽力校验归属）';
COMMENT ON COLUMN error_report.owner_hasn_id IS '归属主人 hasn_id（可空：早期启动/无归属事件）';
COMMENT ON COLUMN error_report.agent_hasn_id IS '归属分身 hasn_id（尽力回填·可空）';
COMMENT ON COLUMN error_report.source IS '来源 (daemon:daemon:blue/hermes:本地hermes:cyan/runtime:云端runtime:purple/webui:前端webui:orange)';
COMMENT ON COLUMN error_report.severity IS '严重度 (critical:致命:red/error:错误:orange/warn:警告:yellow)';
COMMENT ON COLUMN error_report.fingerprint IS '归类键 sha256(source|error_class/归一化消息|模块级位置)，不含行号';
COMMENT ON COLUMN error_report.dedup_key IS '单次物理发生幂等键（客户端稳定哈希，语义见 doc21 §3）';
COMMENT ON COLUMN error_report.error_class IS '异常类/错误码（尽力提取）';
COMMENT ON COLUMN error_report.message IS '脱敏后错误消息';
COMMENT ON COLUMN error_report.location IS 'file:line / logger 名（仅证据，不进 fingerprint）';
COMMENT ON COLUMN error_report.context_json IS '结构化上下文 jsonb（provider/model/binding_id/conv_id/session_id...）';
COMMENT ON COLUMN error_report.suppressed_count IS 'daemon 端采样被抑制的同 fingerprint 次数（累进 issue.occurrence_count 保计数不失真）';
COMMENT ON COLUMN error_report.app_version IS '客户端版本';
COMMENT ON COLUMN error_report.platform IS '平台 (macos/windows/linux/ios/android)';
COMMENT ON COLUMN error_report.occurred_at IS '客户端真实发生时刻（服务端 clamp 到接收时间防异常时钟）';
CREATE UNIQUE INDEX IF NOT EXISTS uq_error_report_dedup ON error_report (node_id, dedup_key);
CREATE INDEX IF NOT EXISTS idx_error_report_fp ON error_report (fingerprint);
CREATE INDEX IF NOT EXISTS idx_error_report_occurred ON error_report (occurred_at);
CREATE INDEX IF NOT EXISTS idx_error_report_owner ON error_report (owner_hasn_id, occurred_at);

-- ========== (2) error_issue — 按 fingerprint 聚合的问题（状态与处理结果单元） ==========
CREATE TABLE IF NOT EXISTS error_issue (
    id bigserial PRIMARY KEY,
    fingerprint varchar(64) NOT NULL,
    title varchar(256) NOT NULL,
    source varchar(16) NOT NULL,
    severity varchar(16) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'investigating', 'resolved', 'skipped', 'wontfix')),
    occurrence_count bigint NOT NULL DEFAULT 0,
    affected_owner_count integer NOT NULL DEFAULT 0,
    affected_node_count integer NOT NULL DEFAULT 0,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    resolution_type varchar(24) CHECK (resolution_type IS NULL OR resolution_type IN ('code_fix', 'config_fix', 'duplicate', 'not_a_bug', 'external', 'cannot_reproduce')),
    resolution_note text,
    duplicate_of_fingerprint varchar(64),
    fixed_in_version varchar(32),
    snooze_until timestamptz,
    issue_url varchar(512),
    pr_url varchar(512),
    resolved_by varchar(64),
    resolved_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE error_issue IS '错误问题（fingerprint 聚合·运维处理与状态单元·长期保留知识资产）';
COMMENT ON COLUMN error_issue.fingerprint IS '归类键（与 error_report.fingerprint 对应，唯一）';
COMMENT ON COLUMN error_issue.title IS '归一化摘要（首条 occurrence 派生）';
COMMENT ON COLUMN error_issue.source IS '来源（该类首见来源）';
COMMENT ON COLUMN error_issue.severity IS '该类最高严重度 (critical/error/warn)';
COMMENT ON COLUMN error_issue.status IS '状态 (open:待处理:gray/investigating:排查中:blue/resolved:已解决:green/skipped:已跳过:orange/wontfix:不予修复:slate)';
COMMENT ON COLUMN error_issue.occurrence_count IS '累计次数（含 suppressed_count 抑制量）';
COMMENT ON COLUMN error_issue.affected_owner_count IS '受影响主人数（累计口径，经 error_issue_seen 去重累加）';
COMMENT ON COLUMN error_issue.affected_node_count IS '受影响设备数（累计口径）';
COMMENT ON COLUMN error_issue.first_seen_at IS '首次发生时刻';
COMMENT ON COLUMN error_issue.last_seen_at IS '末次发生时刻';
COMMENT ON COLUMN error_issue.resolution_type IS '处理方式 (code_fix:代码修复/config_fix:配置修复/duplicate:重复/not_a_bug:非缺陷/external:外部依赖/cannot_reproduce:无法复现)';
COMMENT ON COLUMN error_issue.resolution_note IS '怎么解决 / 为何跳过（resolved/skipped/wontfix 必填）';
COMMENT ON COLUMN error_issue.duplicate_of_fingerprint IS 'resolution_type=duplicate 时必填：归并目标 issue 的 fingerprint';
COMMENT ON COLUMN error_issue.fixed_in_version IS 'resolution_type=code_fix 时必填：修复随哪个版本发布（版本感知重开判据）';
COMMENT ON COLUMN error_issue.snooze_until IS 'skipped 的 snooze 到期时刻（空=无限期直到人工/升级重开）';
COMMENT ON COLUMN error_issue.issue_url IS '关联 GitHub issue';
COMMENT ON COLUMN error_issue.pr_url IS '关联 PR';
COMMENT ON COLUMN error_issue.resolved_by IS '处理分身 hasn_id';
COMMENT ON COLUMN error_issue.resolved_at IS '结案时刻';
CREATE UNIQUE INDEX IF NOT EXISTS uq_error_issue_fp ON error_issue (fingerprint);
CREATE INDEX IF NOT EXISTS idx_error_issue_status ON error_issue (status, last_seen_at);

-- ========== (3) error_issue_event — 处理动作审计流水 ==========
CREATE TABLE IF NOT EXISTS error_issue_event (
    id bigserial PRIMARY KEY,
    fingerprint varchar(64) NOT NULL,
    actor_hasn_id varchar(64) NOT NULL,
    from_status varchar(16),
    to_status varchar(16) NOT NULL,
    note text,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE error_issue_event IS '错误问题处理动作审计流水（谁在何时把状态改成什么）';
COMMENT ON COLUMN error_issue_event.fingerprint IS '归类键';
COMMENT ON COLUMN error_issue_event.actor_hasn_id IS '操作分身 hasn_id；系统自动动作用哨兵 system:auto-reopen';
COMMENT ON COLUMN error_issue_event.from_status IS '原状态（可空：首次创建）';
COMMENT ON COLUMN error_issue_event.to_status IS '新状态';
COMMENT ON COLUMN error_issue_event.note IS '留言 / 重开原因';
CREATE INDEX IF NOT EXISTS idx_error_issue_event_fp ON error_issue_event (fingerprint, created_time);

-- ========== (4) error_issue_seen — affected 计数辅助表 ==========
CREATE TABLE IF NOT EXISTS error_issue_seen (
    id bigserial PRIMARY KEY,
    fingerprint varchar(64) NOT NULL,
    subject_type varchar(8) NOT NULL CHECK (subject_type IN ('owner', 'node')),
    subject_id varchar(64) NOT NULL,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE error_issue_seen IS 'affected 计数辅助表（INSERT ON CONFLICT DO NOTHING，插入成功才 affected_*_count += 1；累计口径 TTL 不回缩）';
COMMENT ON COLUMN error_issue_seen.fingerprint IS '归类键';
COMMENT ON COLUMN error_issue_seen.subject_type IS '主体类型 (owner:主人/node:设备)';
COMMENT ON COLUMN error_issue_seen.subject_id IS '主体 id（owner_hasn_id 或 node_id）';
CREATE UNIQUE INDEX IF NOT EXISTS uq_error_issue_seen ON error_issue_seen (fingerprint, subject_type, subject_id);
