-- 对齐额度账本基表定义，修复早期环境中丢失的时间字段默认值。
SET search_path TO hasn_growth, public;

ALTER TABLE hasn_growth.lead_quota
    ALTER COLUMN created_time SET DEFAULT now(),
    ALTER COLUMN updated_time SET DEFAULT now();

COMMENT ON COLUMN hasn_growth.lead_quota.created_time
    IS '额度账本创建时间，数据库自动生成';
COMMENT ON COLUMN hasn_growth.lead_quota.updated_time
    IS '额度账本最近更新时间，新建时由数据库自动生成';
