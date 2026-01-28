-- =====================================================
-- 用户订阅表 - 管理用户的订阅等级和积分余额 字典数据初始化 SQL
-- 自动生成于: 2026-01-28 15:18:32.383958
-- =====================================================

-- 订阅状态: active/expired/cancelled 字典类型
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES
('订阅状态: active/expired/cancelled', 'llm_status', '用户订阅表 - 管理用户的订阅等级和积分余额模块-订阅状态: active/expired/cancelled', NOW(), NULL)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, remark = EXCLUDED.remark, updated_time = NOW();

-- 订阅状态: active/expired/cancelled 字典数据
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'llm_status' ORDER BY id DESC LIMIT 1;

    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'llm_status' AND value = '1') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('llm_status', '启用', '1', 'green', 1, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'llm_status' AND value = '0') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('llm_status', '禁用', '0', 'red', 2, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 字典数据生成完成
-- =====================================================