-- =====================================================
-- 用户订阅表 - 管理用户的订阅等级和积分余额 字典数据初始化 SQL
-- 自动生成于: 2026-01-28 14:07:27.458895
-- =====================================================

-- 订阅状态: active/expired/cancelled 字典类型
INSERT INTO sys_dict_type (name, code, status, remark, created_time, updated_time)
VALUES
('订阅状态: active/expired/cancelled', 'llm_status', 1, '用户订阅表 - 管理用户的订阅等级和积分余额模块-订阅状态: active/expired/cancelled', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;

-- 订阅状态: active/expired/cancelled 字典数据
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'llm_status' ORDER BY id DESC LIMIT 1;

    INSERT INTO sys_dict_data (label, value, sort, status, color_type, type_id, remark, created_time, updated_time)
    VALUES
    ('启用', '1', 1, 1, 'green', v_dict_type_id, '', NOW(), NULL);
    INSERT INTO sys_dict_data (label, value, sort, status, color_type, type_id, remark, created_time, updated_time)
    VALUES
    ('禁用', '0', 2, 1, 'red', v_dict_type_id, '', NOW(), NULL);
END $$;

-- =====================================================
-- 字典数据生成完成
-- =====================================================