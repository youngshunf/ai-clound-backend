-- =====================================================
-- CreditTransaction 字典数据初始化 SQL
-- 自动生成于: 2026-01-27 19:21:05.359423
-- =====================================================

-- transaction_type 字典类型
INSERT INTO sys_dict_type (name, code, status, remark, created_time, updated_time)
VALUES
('transaction_type', 'llm_transaction_type', 1, 'llm模块-transaction_type', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;

-- transaction_type 字典数据
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'llm_transaction_type' ORDER BY id DESC LIMIT 1;

    INSERT INTO sys_dict_data (label, value, sort, status, color_type, type_id, remark, created_time, updated_time)
    VALUES
    ('类型1', '1', 1, 1, 'blue', v_dict_type_id, '', NOW(), NULL);
    INSERT INTO sys_dict_data (label, value, sort, status, color_type, type_id, remark, created_time, updated_time)
    VALUES
    ('类型2', '2', 2, 1, 'orange', v_dict_type_id, '', NOW(), NULL);
END $$;

-- reference_type 字典类型
INSERT INTO sys_dict_type (name, code, status, remark, created_time, updated_time)
VALUES
('reference_type', 'llm_reference_type', 1, 'llm模块-reference_type', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;

-- reference_type 字典数据
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'llm_reference_type' ORDER BY id DESC LIMIT 1;

    INSERT INTO sys_dict_data (label, value, sort, status, color_type, type_id, remark, created_time, updated_time)
    VALUES
    ('类型1', '1', 1, 1, 'blue', v_dict_type_id, '', NOW(), NULL);
    INSERT INTO sys_dict_data (label, value, sort, status, color_type, type_id, remark, created_time, updated_time)
    VALUES
    ('类型2', '2', 2, 1, 'orange', v_dict_type_id, '', NOW(), NULL);
END $$;

-- =====================================================
-- 字典数据生成完成
-- =====================================================