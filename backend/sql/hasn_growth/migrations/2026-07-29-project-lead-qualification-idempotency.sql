SET search_path TO hasn_growth, public;

-- 项目化后，同一联系人可分别进入不同获客项目；旧主体级唯一键只约束尚未迁移的历史客户。
DROP INDEX IF EXISTS hasn_growth.uq_growth_customer_personal_lead;
DROP INDEX IF EXISTS hasn_growth.uq_growth_customer_enterprise_lead;

CREATE UNIQUE INDEX uq_growth_customer_personal_lead
    ON hasn_growth.customer (user_id, lead_contact_id)
    WHERE owner_scope = 'personal'
      AND growth_project_id IS NULL
      AND lead_contact_id IS NOT NULL;

CREATE UNIQUE INDEX uq_growth_customer_enterprise_lead
    ON hasn_growth.customer (enterprise_id, lead_contact_id)
    WHERE owner_scope = 'enterprise'
      AND growth_project_id IS NULL
      AND lead_contact_id IS NOT NULL;

-- 项目客户与晋级活动均以项目线索为幂等边界，防止并发点击或工具重放产生重复事实。
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_customer_project_lead
    ON hasn_growth.customer (growth_project_id, lead_contact_id)
    WHERE growth_project_id IS NOT NULL
      AND lead_contact_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_activity_project_qualify_ref
    ON hasn_growth.activity (growth_project_id, ref_table, ref_id, kind)
    WHERE growth_project_id IS NOT NULL
      AND ref_table IS NOT NULL
      AND ref_id IS NOT NULL
      AND kind = 'qualify';
