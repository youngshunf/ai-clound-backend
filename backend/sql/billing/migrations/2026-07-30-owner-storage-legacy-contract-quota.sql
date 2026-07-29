-- 用户私有存储上线前生成的旧合同没有 storage_bytes。
-- 这里按当时已固化的 tier 一次性补齐新增权益，只处理缺失键，不覆盖任何已有合同值。
UPDATE hasn_billing.user_subscription
SET plan_snapshot = jsonb_set(
    COALESCE(plan_snapshot, '{}'::jsonb),
    '{storage_bytes}',
    to_jsonb(
        CASE tier
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
WHERE app_code = 'huanxing'
  AND tier IN ('free', 'pro', 'max', 'advanced', 'ultra', 'flagship')
  AND (
      plan_snapshot IS NULL
      OR (
          jsonb_typeof(plan_snapshot) = 'object'
          AND NOT (plan_snapshot ? 'storage_bytes')
      )
  );
