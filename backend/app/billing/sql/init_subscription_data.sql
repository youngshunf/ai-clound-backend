-- ============================================================
-- 订阅配置数据初始化 - 唤星 + 智小芽 (PostgreSQL)
-- 日期: 2026-02-23
-- 说明:
--   1. 将现有 subscription_tier 数据的 app_code 改为 zhixiaoya
--   2. 新增唤星 (huanxing) 的订阅等级配置
--   3. 将现有 credit_package 数据的 app_code 改为 zhixiaoya
--   4. 新增唤星 (huanxing) 的积分包配置
-- ============================================================

-- ==================== 0. 修改唯一约束：tier_name → (tier_name, app_code) ====================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_subscription_tier_name_app') THEN
    ALTER TABLE subscription_tier DROP CONSTRAINT IF EXISTS subscription_tier_tier_name_key;
    ALTER TABLE subscription_tier ADD CONSTRAINT uq_subscription_tier_name_app UNIQUE (tier_name, app_code);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_credit_package_name_app') THEN
    ALTER TABLE credit_package DROP CONSTRAINT IF EXISTS credit_package_package_name_key;
    ALTER TABLE credit_package ADD CONSTRAINT uq_credit_package_name_app UNIQUE (package_name, app_code);
  END IF;
END$$;

-- ==================== 1. 修改现有数据为智小芽 ====================
UPDATE subscription_tier SET app_code = 'zhixiaoya' WHERE app_code = 'huanxing';
UPDATE credit_package SET app_code = 'zhixiaoya' WHERE app_code = 'huanxing';

-- 同时修改现有用户订阅、积分余额、交易记录为智小芽
UPDATE user_subscription SET app_code = 'zhixiaoya' WHERE app_code = 'huanxing';
UPDATE user_credit_balance SET app_code = 'zhixiaoya' WHERE app_code = 'huanxing';
UPDATE credit_transaction SET app_code = 'zhixiaoya' WHERE app_code = 'huanxing';
UPDATE model_credit_rate SET app_code = 'zhixiaoya' WHERE app_code = 'huanxing';

-- ==================== 2. 更新智小芽现有订阅等级的 features 为中文 ====================
UPDATE subscription_tier SET features = '{"每日对话次数": 10, "客服支持": "社区支持", "每月积分": 100}'::jsonb
WHERE app_code = 'zhixiaoya' AND tier_name = 'free';

UPDATE subscription_tier SET features = '{"每日对话次数": 60, "客服支持": "邮件支持", "长期记忆": true, "多设备同步": true, "每月积分": 1000}'::jsonb
WHERE app_code = 'zhixiaoya' AND tier_name = 'basic';

UPDATE subscription_tier SET features = '{"每日对话次数": 120, "客服支持": "优先支持", "长期记忆": true, "工具连接": true, "每月积分": 5000}'::jsonb
WHERE app_code = 'zhixiaoya' AND tier_name = 'pro';

UPDATE subscription_tier SET features = '{"每日对话次数": "无限", "客服支持": "专属客服", "专属模型": true, "API 接口": true, "多分身支持": true, "每月积分": 50000}'::jsonb
WHERE app_code = 'zhixiaoya' AND tier_name = 'enterprise';

-- ==================== 3. 新增唤星订阅等级 ====================
-- 唤星与智小芽共用积分体系，区别在于功能特性描述

INSERT INTO subscription_tier (app_code, tier_name, display_name, monthly_credits, monthly_price, yearly_price, yearly_discount, features, enabled, sort_order, created_time)
VALUES
(
  'huanxing', 'free', '微星',
  100, 0, NULL, NULL,
  '{"每月积分": 100, "可用模型": "基础模型", "记忆保存": "7天", "客服支持": "社区支持"}'::jsonb,
  true, 1, NOW()
),
(
  'huanxing', 'pro', '明星',
  1000, 128, 1228, 0.8,
  '{"每月积分": 1000, "可用模型": "基础+进阶模型", "记忆存储": true, "版本管理": true, "客服支持": "邮件支持"}'::jsonb,
  true, 2, NOW()
),
(
  'huanxing', 'advanced', '恒星',
  5000, 238, 2284, 0.8,
  '{"每月积分": 5000, "可用模型": "全部模型", "记忆存储": true, "版本管理": true, "文件备份": true, "云存储": true, "客服支持": "优先支持"}'::jsonb,
  true, 3, NOW()
),
(
  'huanxing', 'flagship', '超新星',
  50000, 598, 5740, 0.8,
  '{"每月积分": 50000, "可用模型": "全部模型+专属部署", "记忆存储": true, "版本管理": true, "文件备份": true, "云存储": true, "专属模型通道": true, "SLA 保障": true, "客服支持": "专属客服"}'::jsonb,
  true, 4, NOW()
);

-- ==================== 4. 新增唤星积分包 ====================

INSERT INTO credit_package (app_code, package_name, credits, price, bonus_credits, description, enabled, sort_order, created_time)
VALUES
(
  'huanxing', '入门包', 1000, 9.9, 100,
  '适合轻度 API 调用用户', true, 1, NOW()
),
(
  'huanxing', '标准包', 5000, 39.9, 800,
  '适合日常开发测试', true, 2, NOW()
),
(
  'huanxing', '专业包', 20000, 128, 5000,
  '适合生产环境稳定调用', true, 3, NOW()
),
(
  'huanxing', '企业包', 100000, 498, 30000,
  '大规模 API 调用，性价比最高', true, 4, NOW()
);
