-- 为 LLM 套餐登记整数 bytes 存储权益；只补缺失值，不覆盖运营已配置的合同口径。
UPDATE hasn_billing.billing_plan
SET quota_json = jsonb_set(
    quota_json,
    '{storage_bytes}',
    to_jsonb(
        CASE quota_json ->> 'tier'
            WHEN 'free' THEN 10737418240::BIGINT
            WHEN 'pro' THEN 107374182400::BIGINT
            WHEN 'max' THEN 107374182400::BIGINT
            WHEN 'advanced' THEN 536870912000::BIGINT
            WHEN 'ultra' THEN 536870912000::BIGINT
            WHEN 'flagship' THEN 1099511627776::BIGINT
        END
    ),
    TRUE
)
WHERE offering_key = 'llm:tier'
  AND NOT (quota_json ? 'storage_bytes')
  AND quota_json ->> 'tier' IN ('free', 'pro', 'max', 'advanced', 'ultra', 'flagship');

-- 未识别档位故意不回落免费配额；必须由运营显式配置后才能建立新合同。
