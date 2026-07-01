-- =====================================================
-- 订阅系统初始化数据 (PostgreSQL)
-- 包含：订阅等级、积分包、模型积分费率
-- =====================================================

-- =====================================================
-- 1. 订阅等级配置 (subscription_tier)
-- 积分说明：
--   - free: 500积分/月 (首月，之后需付费)
--   - pro: 1000积分/月
--   - max: 2000积分/月
--   - ultra: 5000积分/月
-- =====================================================

INSERT INTO subscription_tier (tier_name, display_name, monthly_credits, monthly_price, features, enabled, sort_order, created_time)
VALUES 
    ('free', '免费版', 500, 0, 
     '{"api_access": true, "max_rpm": 10, "max_daily_requests": 100, "support": "community"}'::jsonb, 
     true, 0, NOW()),
    ('pro', '专业版', 1000, 29, 
     '{"api_access": true, "max_rpm": 60, "max_daily_requests": 1000, "support": "email", "priority_queue": true}'::jsonb, 
     true, 1, NOW()),
    ('max', '高级版', 2000, 79, 
     '{"api_access": true, "max_rpm": 120, "max_daily_requests": 5000, "support": "priority", "priority_queue": true, "custom_models": true}'::jsonb, 
     true, 2, NOW()),
    ('ultra', '旗舰版', 5000, 199, 
     '{"api_access": true, "max_rpm": 300, "max_daily_requests": -1, "support": "dedicated", "priority_queue": true, "custom_models": true, "dedicated_capacity": true}'::jsonb, 
     true, 3, NOW())
ON CONFLICT (tier_name) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    monthly_credits = EXCLUDED.monthly_credits,
    monthly_price = EXCLUDED.monthly_price,
    features = EXCLUDED.features,
    enabled = EXCLUDED.enabled,
    sort_order = EXCLUDED.sort_order,
    updated_time = NOW();

-- =====================================================
-- 2. 积分包配置 (credit_package)
-- 用户可单独购买的积分包
-- =====================================================

INSERT INTO credit_package (package_name, credits, price, bonus_credits, description, enabled, sort_order, created_time)
VALUES 
    ('入门包', 100, 9.9, 0, '适合轻度使用', true, 0, NOW()),
    ('基础包', 500, 39, 50, '赠送50积分，适合日常使用', true, 1, NOW()),
    ('标准包', 1000, 69, 150, '赠送150积分，性价比之选', true, 2, NOW()),
    ('专业包', 3000, 179, 600, '赠送600积分，专业用户首选', true, 3, NOW()),
    ('企业包', 10000, 499, 2500, '赠送2500积分，大量使用优惠', true, 4, NOW())
ON CONFLICT DO NOTHING;

-- =====================================================
-- 3. 全局积分转换配置
-- 标准比例：百万 token = 输入 5 积分 / 输出 15 积分
-- 即：1K token = 输入 0.005 积分 / 输出 0.015 积分
-- 
-- 计算公式：
--   积分 = (input_tokens/1000 * base_credit * input_multiplier) 
--        + (output_tokens/1000 * base_credit * output_multiplier)
-- 
-- 标准配置 (base_credit=1, input_mult=0.005, output_mult=0.015):
--   1M input tokens = 1000 * 1 * 0.005 = 5 积分
--   1M output tokens = 1000 * 1 * 0.015 = 15 积分
-- =====================================================

-- 创建全局配置表（如果不存在）
CREATE TABLE IF NOT EXISTS system_config (
    id SERIAL PRIMARY KEY,
    config_key VARCHAR(64) UNIQUE NOT NULL,
    config_value JSONB NOT NULL,
    description VARCHAR(256),
    created_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_time TIMESTAMP WITH TIME ZONE
);

-- 插入全局积分转换配置
INSERT INTO system_config (config_key, config_value, description, created_time)
VALUES (
    'credit_conversion',
    '{
        "base_credit_per_1k_tokens": 1.0,
        "default_input_multiplier": 5,
        "default_output_multiplier": 15,
        "description": "标准比例: 1M tokens = 输入5积分/输出15积分"
    }'::jsonb,
    '积分转换全局配置',
    NOW()
)
ON CONFLICT (config_key) DO UPDATE SET
    config_value = EXCLUDED.config_value,
    updated_time = NOW();

-- =====================================================
-- 4. 模型积分费率 (model_credit_rate)
-- 基于 llm_model_config 表中的模型配置
-- 
-- 费率倍率说明：
--   - GPT-4o, Claude 3.5 Sonnet 等主流模型: 1.0x (标准费率)
--   - GPT-4 Turbo, Claude 3 Opus 等高端模型: 2.0x
--   - GPT-3.5, Claude 3 Haiku 等轻量模型: 0.5x
--   - 特殊模型根据实际成本调整
--
-- 计算示例 (使用 Claude 3.5 Sonnet, multiplier=1.0):
--   1M input tokens = 1000 * 1.0 * 0.005 * 1.0 = 5 积分
--   1M output tokens = 1000 * 1.0 * 0.015 * 1.0 = 15 积分
-- =====================================================

-- 先为所有已存在的模型创建默认费率配置
INSERT INTO model_credit_rate (model_id, base_credit_per_1k_tokens, input_multiplier, output_multiplier, enabled, created_time)
SELECT 
    mc.id as model_id,
    1.0 as base_credit_per_1k_tokens,
    CASE 
        -- 轻量模型 0.5x
        WHEN mc.model_name ILIKE '%gpt-3.5%' THEN 0.0025
        WHEN mc.model_name ILIKE '%haiku%' THEN 0.0025
        WHEN mc.model_name ILIKE '%mini%' THEN 0.0025
        -- 高端模型 2.0x
        WHEN mc.model_name ILIKE '%gpt-4-turbo%' THEN 0.01
        WHEN mc.model_name ILIKE '%gpt-4-32k%' THEN 0.01
        WHEN mc.model_name ILIKE '%opus%' THEN 0.01
        WHEN mc.model_name ILIKE '%o1%' THEN 0.02
        -- 标准模型 1.0x
        ELSE 0.005
    END as input_multiplier,
    CASE 
        -- 轻量模型 0.5x
        WHEN mc.model_name ILIKE '%gpt-3.5%' THEN 0.0075
        WHEN mc.model_name ILIKE '%haiku%' THEN 0.0075
        WHEN mc.model_name ILIKE '%mini%' THEN 0.0075
        -- 高端模型 2.0x
        WHEN mc.model_name ILIKE '%gpt-4-turbo%' THEN 0.03
        WHEN mc.model_name ILIKE '%gpt-4-32k%' THEN 0.03
        WHEN mc.model_name ILIKE '%opus%' THEN 0.03
        WHEN mc.model_name ILIKE '%o1%' THEN 0.06
        -- 标准模型 1.0x
        ELSE 0.015
    END as output_multiplier,
    true as enabled,
    NOW() as created_time
FROM llm_model_config mc
WHERE mc.enabled = true
AND NOT EXISTS (
    SELECT 1 FROM model_credit_rate mcr WHERE mcr.model_id = mc.id
);

-- =====================================================
-- 5. 为 subscription_tier 表添加唯一约束（如果不存在）
-- =====================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'subscription_tier_tier_name_key'
    ) THEN
        ALTER TABLE subscription_tier ADD CONSTRAINT subscription_tier_tier_name_key UNIQUE (tier_name);
    END IF;
END $$;

-- =====================================================
-- 初始化完成
-- 
-- 积分消耗示例（以 Claude 3.5 Sonnet 为例，标准费率 1.0x）:
--   - 输入 100K tokens: 100 * 1.0 * 0.005 = 0.5 积分
--   - 输出 10K tokens: 10 * 1.0 * 0.015 = 0.15 积分
--   - 总计: 0.65 积分
--
-- 免费用户（500积分）大约可以:
--   - 约 77 次对话（假设每次 100K 输入 + 10K 输出）
--   - 或约 100M 输入 tokens + 10M 输出 tokens
-- =====================================================
