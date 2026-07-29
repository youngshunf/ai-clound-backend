-- 获客营销全链路应用 hasn_growth：7 张新表（设计 07 §5.2）
-- 落 schema hasn_growth（与采集子域收编的 10 表同 schema）；PostgreSQL 语法。
-- codegen 两步：--sql-file 建表（--schema hasn_growth）→ --table 批量生成代码。
-- FK：customer.lead_contact_id→contact（同 schema 物理外键，v1.2 收编后安全）；
--     opportunity.customer_id→customer；followup_task_id/task_run_id 是跨 schema(hasn_task) 逻辑引用不建物理 FK。

SET search_path TO hasn_growth, public;

-- ========== 1. customer 客户 ==========
CREATE TABLE IF NOT EXISTS customer (
    id bigserial PRIMARY KEY,
    customer_no varchar(40) NOT NULL UNIQUE,
    user_id bigint NOT NULL,
    lead_contact_id bigint REFERENCES contact(id),
    source_kind varchar(24) NOT NULL DEFAULT 'manual',
    company_name varchar(255),
    contact_name varchar(100),
    email varchar(255),
    phone varchar(50),
    wechat varchar(100),
    im_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
    profile_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    intent_score numeric(5,2) NOT NULL DEFAULT 0,
    lifecycle_status varchar(16) NOT NULL DEFAULT 'active',
    owner_agent_id varchar(64),
    followup_task_id varchar(64),
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_activity_at timestamptz,
    next_followup_at timestamptz,
    silent_round_count int NOT NULL DEFAULT 0,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_customer_user_lead UNIQUE (user_id, lead_contact_id)
);
COMMENT ON TABLE customer IS '获客客户（qualified 线索 / inbound 直建）';
COMMENT ON COLUMN customer.source_kind IS '来源 (outbound_crawl:采集:blue/inbound_form:留资:green/community:内容:purple/manual:手动:gray)';
COMMENT ON COLUMN customer.lifecycle_status IS '生命周期 (active:跟进中:blue/engaged:有回应:cyan/opportunity:已立商机:purple/silent:沉默:gray/won:成交:green/lost:流失:red/archived:归档:gray)';
COMMENT ON COLUMN customer.profile_json IS 'AI 画像（行业/规模/痛点/预算信号/决策角色/沟通偏好）';
COMMENT ON COLUMN customer.intent_score IS '意向分（分身每次跟进后更新）';
COMMENT ON COLUMN customer.followup_task_id IS '当前跟进任务（hasn_task.task.id 逻辑引用）';
CREATE INDEX IF NOT EXISTS idx_growth_customer_user_status ON customer (user_id, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_growth_customer_user_score ON customer (user_id, intent_score DESC);
CREATE INDEX IF NOT EXISTS idx_growth_customer_owner_agent ON customer (owner_agent_id);
CREATE INDEX IF NOT EXISTS idx_growth_customer_next_followup ON customer (next_followup_at);

-- ========== 2. opportunity 商机 ==========
CREATE TABLE IF NOT EXISTS opportunity (
    id bigserial PRIMARY KEY,
    opportunity_no varchar(40) NOT NULL UNIQUE,
    customer_id bigint NOT NULL REFERENCES customer(id),
    user_id bigint NOT NULL,
    name varchar(200) NOT NULL,
    version bigint NOT NULL DEFAULT 1,
    stage varchar(24) NOT NULL DEFAULT 'contacted',
    amount numeric(14,2),
    currency varchar(8) NOT NULL DEFAULT 'CNY',
    probability numeric(5,2),
    expected_close_at timestamptz,
    won_at timestamptz,
    lost_at timestamptz,
    lost_reason varchar(500),
    close_note text,
    review_task_id varchar(64),
    created_by_kind varchar(16) NOT NULL DEFAULT 'agent',
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT ck_growth_opportunity_version CHECK (version >= 1)
);
COMMENT ON TABLE opportunity IS '获客商机（阶段推进 + 金额 + 成交/败因登记）';
COMMENT ON COLUMN opportunity.stage IS '阶段 (contacted:已触达:blue/replied:已回应:cyan/proposal:已发提案:purple/negotiation:商务洽谈:orange/closed_won:成交:green/closed_lost:流失:red)';
COMMENT ON COLUMN opportunity.version IS '并发控制版本；每次阶段变化或关闭单调递增';
COMMENT ON COLUMN opportunity.review_task_id IS '成交或流失后幂等创建的复盘任务 UUID';
COMMENT ON COLUMN opportunity.created_by_kind IS '创建者 (owner:主人:blue/agent:分身:violet)';
CREATE INDEX IF NOT EXISTS idx_growth_opportunity_user_stage ON opportunity (user_id, stage);
CREATE INDEX IF NOT EXISTS idx_growth_opportunity_customer ON opportunity (customer_id);
CREATE INDEX IF NOT EXISTS idx_growth_opportunity_expected_close ON opportunity (expected_close_at);

-- ========== 3. outreach_message 触达消息（审批状态机核心） ==========
CREATE TABLE IF NOT EXISTS outreach_message (
    id bigserial PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customer(id),
    opportunity_id bigint,
    user_id bigint NOT NULL,
    agent_id varchar(64),
    direction varchar(8) NOT NULL DEFAULT 'outbound',
    channel varchar(24) NOT NULL,
    subject varchar(200),
    content text NOT NULL,
    content_assets jsonb NOT NULL DEFAULT '[]'::jsonb,
    status varchar(24) NOT NULL DEFAULT 'draft',
    intent_note varchar(500),
    approval_user_id bigint,
    approved_at timestamptz,
    reject_reason varchar(500),
    auto_approved boolean NOT NULL DEFAULT false,
    task_run_id varchar(64),
    workflow_run_id varchar(64),
    sent_at timestamptz,
    replied_at timestamptz,
    error_message text,
    compliance_check jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key varchar(64),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_outreach_dedupe UNIQUE (dedupe_key)
);
COMMENT ON TABLE outreach_message IS '获客触达消息（出/入双向，审批状态机核心表）';
COMMENT ON COLUMN outreach_message.direction IS '方向 (outbound:出:blue/inbound:入:green)';
COMMENT ON COLUMN outreach_message.channel IS '渠道 (manual_assist:人工辅助:gray/wechat:微信:green/qq:QQ:blue/feishu:飞书:cyan/email:邮件:orange/hasn_dm:站内:purple)';
COMMENT ON COLUMN outreach_message.status IS '状态 (draft:草稿:gray/pending_approval:待审批:orange/approved:已批准:blue/sending:发送中:cyan/sent:已发送:green/replied:已回复:purple/rejected:已拒绝:red/failed:失败:red/blocked_optout:退订拦截:red/blocked_compliance:合规拦截:red)';
COMMENT ON COLUMN outreach_message.auto_approved IS '白名单放行标记（审计区分人批/自动）';
COMMENT ON COLUMN outreach_message.intent_note IS '给主人看的一句话：为什么现在发这条';
CREATE INDEX IF NOT EXISTS idx_growth_outreach_user_status ON outreach_message (user_id, status);
CREATE INDEX IF NOT EXISTS idx_growth_outreach_customer_time ON outreach_message (customer_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_growth_outreach_status_channel ON outreach_message (status, channel);

-- ========== 4. activity 客户活动时间线 ==========
CREATE TABLE IF NOT EXISTS activity (
    id bigserial PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customer(id),
    opportunity_id bigint,
    user_id bigint NOT NULL,
    kind varchar(24) NOT NULL,
    content text,
    actor_kind varchar(8),
    actor_id varchar(64),
    ref_table varchar(32),
    ref_id varchar(64),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE activity IS '获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）';
COMMENT ON COLUMN activity.kind IS '类型 (outreach:触达:blue/reply:回复:green/stage_change:阶段变更:orange/task_run:任务:cyan/note:备注:gray/call:电话:purple/meeting:会议:purple/qualify:晋级:blue/close:成交:green)';
COMMENT ON COLUMN activity.actor_kind IS '执行者 (owner:主人:blue/agent:分身:violet)';
CREATE INDEX IF NOT EXISTS idx_growth_activity_customer_time ON activity (customer_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_growth_activity_user ON activity (user_id);

-- ========== 5. playbook 获客打法模板 ==========
CREATE TABLE IF NOT EXISTS playbook (
    id bigserial PRIMARY KEY,
    user_id bigint,
    name varchar(200) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    goal text,
    target_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    cadence jsonb NOT NULL DEFAULT '[]'::jsonb,
    tone_guide text,
    exit_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_builtin boolean NOT NULL DEFAULT false,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE playbook IS '获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义';
COMMENT ON COLUMN playbook.user_id IS '归属主人（可空=内置 playbook）';
COMMENT ON COLUMN playbook.cadence IS '触达节奏 [{day,channel,goal}]';
COMMENT ON COLUMN playbook.exit_rule IS '止损规则 {max_silent_rounds,action}';
CREATE INDEX IF NOT EXISTS idx_growth_playbook_user ON playbook (user_id);

-- ========== 6. form_submission 落地页表单回流 ==========
CREATE TABLE IF NOT EXISTS form_submission (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    publish_ref varchar(128),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    email varchar(255),
    phone varchar(50),
    name varchar(100),
    company varchar(255),
    status varchar(16) NOT NULL DEFAULT 'pending',
    customer_id bigint,
    source_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE form_submission IS '获客落地页表单回流（inbound 线索缓冲区）';
COMMENT ON COLUMN form_submission.status IS '状态 (pending:待处理:gray/converted:已转化:green/rejected:已拒绝:red/spam:垃圾:red)';
COMMENT ON COLUMN form_submission.source_meta IS 'UTM/referrer/IP hash（反滥用 + 归因）';
CREATE INDEX IF NOT EXISTS idx_growth_form_user_status ON form_submission (user_id, status);
CREATE INDEX IF NOT EXISTS idx_growth_form_publish_ref ON form_submission (publish_ref);

-- ========== 7. optout_record 退订/勿扰登记（合规硬约束） ==========
CREATE TABLE IF NOT EXISTS optout_record (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    channel varchar(24) NOT NULL DEFAULT 'all',
    address_hash varchar(64) NOT NULL,
    customer_id bigint,
    reason varchar(500),
    source varchar(64),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_optout_user_channel_addr UNIQUE (user_id, channel, address_hash)
);
COMMENT ON TABLE optout_record IS '获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）';
COMMENT ON COLUMN optout_record.channel IS '渠道；all=全渠道';
COMMENT ON COLUMN optout_record.address_hash IS 'sha256(归一化联系方式)——不存明文';
CREATE INDEX IF NOT EXISTS idx_growth_optout_user_addr ON optout_record (user_id, address_hash);
