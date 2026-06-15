-- =====================================================
-- 获客 enterprise 化数据层双模化（实施 92 GE1 / 设计 v3 §6.7 业务系统型应用）
--
-- 把 hasn_growth 核心业务表从「user_id 单主人 CRM」升级为「企业租户共享池 + 负责人分配」双模：
--   * owner_scope：personal（个人主人，holder=user_id）| enterprise（企业租户，holder=enterprise_id）
--   * enterprise_id：enterprise 模式下的企业 ID（personal 模式 NULL）
--   * assignee：enterprise 模式下的负责人 hasn_id（跟进人；与 owner_agent_id「哪个分身在跟」并存别混）
--
-- 存量零回退：所有现有行 owner_scope='personal'、enterprise_id=NULL、assignee=NULL（个人模式行为不变）。
-- 幂等：ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / DROP ... IF EXISTS，可重复执行。
-- 纯加列，不重 codegen（CLAUDE.md：字段增减走迁移）。
-- =====================================================

-- ---------- customer：双模归属 + 负责人 ----------
ALTER TABLE hasn_growth.customer ADD COLUMN IF NOT EXISTS owner_scope   VARCHAR(16) NOT NULL DEFAULT 'personal';
ALTER TABLE hasn_growth.customer ADD COLUMN IF NOT EXISTS enterprise_id BIGINT;
ALTER TABLE hasn_growth.customer ADD COLUMN IF NOT EXISTS assignee      VARCHAR(64);
COMMENT ON COLUMN hasn_growth.customer.owner_scope   IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_growth.customer.enterprise_id IS '企业 ID（enterprise 模式；personal 为 NULL）';
COMMENT ON COLUMN hasn_growth.customer.assignee      IS '负责人 hasn_id（enterprise 模式跟进人；与 owner_agent_id 分身维度并存）';

-- customer 唯一键双模改造：旧 UNIQUE(user_id, lead_contact_id) → 按 owner_scope 拆两个 partial unique。
-- personal 仍按 (user_id, lead_contact_id)；enterprise 去重维度改 (enterprise_id, lead_contact_id)。
-- NULL lead_contact_id（手动/inbound 客户）天然不进唯一约束（PG 多 NULL 不冲突），与旧行为一致。
ALTER TABLE hasn_growth.customer DROP CONSTRAINT IF EXISTS uq_growth_customer_user_lead;
DROP INDEX IF EXISTS hasn_growth.uq_growth_customer_user_lead;
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_customer_personal_lead
    ON hasn_growth.customer (user_id, lead_contact_id)
    WHERE owner_scope = 'personal' AND lead_contact_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_customer_enterprise_lead
    ON hasn_growth.customer (enterprise_id, lead_contact_id)
    WHERE owner_scope = 'enterprise' AND lead_contact_id IS NOT NULL;
-- 企业热查询索引：经理按企业全量列、销售按 assignee 裁剪，常带 lifecycle_status 过滤。
CREATE INDEX IF NOT EXISTS idx_growth_customer_ent_assignee
    ON hasn_growth.customer (enterprise_id, assignee, lifecycle_status);

-- ---------- opportunity：双模 + 负责人（看板「全员/我的」按此裁剪） ----------
ALTER TABLE hasn_growth.opportunity ADD COLUMN IF NOT EXISTS owner_scope   VARCHAR(16) NOT NULL DEFAULT 'personal';
ALTER TABLE hasn_growth.opportunity ADD COLUMN IF NOT EXISTS enterprise_id BIGINT;
ALTER TABLE hasn_growth.opportunity ADD COLUMN IF NOT EXISTS assignee      VARCHAR(64);
COMMENT ON COLUMN hasn_growth.opportunity.owner_scope   IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_growth.opportunity.enterprise_id IS '企业 ID（enterprise 模式；personal 为 NULL）';
COMMENT ON COLUMN hasn_growth.opportunity.assignee      IS '负责人 hasn_id（enterprise 模式）';
CREATE INDEX IF NOT EXISTS idx_growth_opportunity_ent_assignee
    ON hasn_growth.opportunity (enterprise_id, assignee, stage);

-- ---------- outreach_message：双模 + 负责人（审批按 assignee 主人维度） ----------
ALTER TABLE hasn_growth.outreach_message ADD COLUMN IF NOT EXISTS owner_scope   VARCHAR(16) NOT NULL DEFAULT 'personal';
ALTER TABLE hasn_growth.outreach_message ADD COLUMN IF NOT EXISTS enterprise_id BIGINT;
ALTER TABLE hasn_growth.outreach_message ADD COLUMN IF NOT EXISTS assignee      VARCHAR(64);
COMMENT ON COLUMN hasn_growth.outreach_message.owner_scope   IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_growth.outreach_message.enterprise_id IS '企业 ID（enterprise 模式；personal 为 NULL）';
COMMENT ON COLUMN hasn_growth.outreach_message.assignee      IS '负责人 hasn_id（enterprise 模式，审批归其主人）';
CREATE INDEX IF NOT EXISTS idx_growth_outreach_ent_assignee
    ON hasn_growth.outreach_message (enterprise_id, assignee, status);

-- ---------- activity：双模 + 负责人（继承客户的归属，便于企业全量时间线聚合） ----------
ALTER TABLE hasn_growth.activity ADD COLUMN IF NOT EXISTS owner_scope   VARCHAR(16) NOT NULL DEFAULT 'personal';
ALTER TABLE hasn_growth.activity ADD COLUMN IF NOT EXISTS enterprise_id BIGINT;
ALTER TABLE hasn_growth.activity ADD COLUMN IF NOT EXISTS assignee      VARCHAR(64);
COMMENT ON COLUMN hasn_growth.activity.owner_scope   IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_growth.activity.enterprise_id IS '企业 ID（enterprise 模式；personal 为 NULL）';
COMMENT ON COLUMN hasn_growth.activity.assignee      IS '负责人 hasn_id（enterprise 模式，承自客户）';
CREATE INDEX IF NOT EXISTS idx_growth_activity_ent
    ON hasn_growth.activity (enterprise_id, customer_id);

-- ---------- form_submission：双模 + 负责人（inbound 留资归企业池/分配） ----------
ALTER TABLE hasn_growth.form_submission ADD COLUMN IF NOT EXISTS owner_scope   VARCHAR(16) NOT NULL DEFAULT 'personal';
ALTER TABLE hasn_growth.form_submission ADD COLUMN IF NOT EXISTS enterprise_id BIGINT;
ALTER TABLE hasn_growth.form_submission ADD COLUMN IF NOT EXISTS assignee      VARCHAR(64);
COMMENT ON COLUMN hasn_growth.form_submission.owner_scope   IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_growth.form_submission.enterprise_id IS '企业 ID（enterprise 模式；personal 为 NULL）';
COMMENT ON COLUMN hasn_growth.form_submission.assignee      IS '负责人 hasn_id（enterprise 模式）';

-- ---------- playbook：双模归属（企业级 playbook，GE3 企业开通自播种） ----------
-- playbook 是配置/模板，无个人负责人维度，只加 owner_scope + enterprise_id。
-- 内置（is_builtin + user_id IS NULL）对所有人可见；enterprise playbook（owner_scope='enterprise'）仅本企业可见可编辑。
ALTER TABLE hasn_growth.playbook ADD COLUMN IF NOT EXISTS owner_scope   VARCHAR(16) NOT NULL DEFAULT 'personal';
ALTER TABLE hasn_growth.playbook ADD COLUMN IF NOT EXISTS enterprise_id BIGINT;
COMMENT ON COLUMN hasn_growth.playbook.owner_scope   IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN hasn_growth.playbook.enterprise_id IS '企业 ID（enterprise 模式；personal/内置 为 NULL）';
-- 内置行 owner_scope 归一为 personal 语义（用 is_builtin 判定，不靠 owner_scope）；显式回填确保非 NULL。
UPDATE hasn_growth.playbook SET owner_scope = 'personal' WHERE owner_scope IS NULL;
-- 企业级 playbook 幂等键：同企业内 name 唯一（GE3 ensure_growth_enterprise_seeded 据此 ON CONFLICT）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_playbook_enterprise_name
    ON hasn_growth.playbook (enterprise_id, name)
    WHERE owner_scope = 'enterprise' AND enterprise_id IS NOT NULL;
