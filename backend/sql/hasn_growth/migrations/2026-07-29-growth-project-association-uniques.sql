SET search_path TO hasn_growth, public;

-- 同一获客项目内，同一条线索只能关联一个客户记录。
-- 保留既有 user_id + lead_contact_id 唯一约束，避免在 S1 改变历史语义。
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_customer_project_lead
    ON customer (growth_project_id, lead_contact_id)
    WHERE growth_project_id IS NOT NULL
      AND lead_contact_id IS NOT NULL;
