-- ============================================================
-- 多应用订阅隔离 - 数据库迁移脚本 (PostgreSQL)
-- 日期: 2026-02-23
-- 说明: 给订阅相关的 6 张表加 app_code 字段，插入字典数据
-- ============================================================

-- 1. 添加 app_code 字段（默认值 'huanxing'，兼容现有数据）
ALTER TABLE subscription_tier ADD COLUMN app_code VARCHAR(32) NOT NULL DEFAULT 'huanxing';
ALTER TABLE credit_package ADD COLUMN app_code VARCHAR(32) NOT NULL DEFAULT 'huanxing';
ALTER TABLE user_subscription ADD COLUMN app_code VARCHAR(32) NOT NULL DEFAULT 'huanxing';
ALTER TABLE user_credit_balance ADD COLUMN app_code VARCHAR(32) NOT NULL DEFAULT 'huanxing';
ALTER TABLE credit_transaction ADD COLUMN app_code VARCHAR(32) NOT NULL DEFAULT 'huanxing';
ALTER TABLE model_credit_rate ADD COLUMN app_code VARCHAR(32) NOT NULL DEFAULT 'huanxing';

-- 字段注释
COMMENT ON COLUMN subscription_tier.app_code IS '应用标识 (huanxing:唤星/zhixiaoya:智小芽)';
COMMENT ON COLUMN credit_package.app_code IS '应用标识 (huanxing:唤星/zhixiaoya:智小芽)';
COMMENT ON COLUMN user_subscription.app_code IS '应用标识 (huanxing:唤星/zhixiaoya:智小芽)';
COMMENT ON COLUMN user_credit_balance.app_code IS '应用标识 (huanxing:唤星/zhixiaoya:智小芽)';
COMMENT ON COLUMN credit_transaction.app_code IS '应用标识 (huanxing:唤星/zhixiaoya:智小芽)';
COMMENT ON COLUMN model_credit_rate.app_code IS '应用标识 (huanxing:唤星/zhixiaoya:智小芽)';

-- 2. 添加索引
CREATE INDEX idx_subscription_tier_app ON subscription_tier(app_code);
CREATE INDEX idx_credit_package_app ON credit_package(app_code);
CREATE INDEX idx_user_subscription_user_app ON user_subscription(user_id, app_code);
CREATE INDEX idx_user_credit_balance_user_app ON user_credit_balance(user_id, app_code);
CREATE INDEX idx_credit_transaction_user_app ON credit_transaction(user_id, app_code);
CREATE INDEX idx_model_credit_rate_app ON model_credit_rate(app_code);

-- 3. user_subscription 唯一约束：同一用户在同一应用下只能有一条订阅
-- 注意：如果已有 user_id 的唯一约束，需要先删除
-- ALTER TABLE user_subscription DROP CONSTRAINT IF EXISTS <旧唯一约束名>;
ALTER TABLE user_subscription ADD CONSTRAINT uq_user_subscription_user_app UNIQUE (user_id, app_code);

-- 4. 插入字典类型
INSERT INTO sys_dict_type (name, code, remark, created_time)
VALUES ('应用标识', 'sys_app_code', '多应用隔离标识', NOW());

-- 5. 插入字典数据
INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, created_time)
SELECT 'sys_app_code', '唤星AI', 'huanxing', 'processing', 1, 1, id, NOW()
FROM sys_dict_type WHERE code = 'sys_app_code';

INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, created_time)
SELECT 'sys_app_code', '智小芽', 'zhixiaoya', 'success', 2, 1, id, NOW()
FROM sys_dict_type WHERE code = 'sys_app_code';
