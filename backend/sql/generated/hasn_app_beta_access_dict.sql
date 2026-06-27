-- =====================================================
-- AI-Native 应用灰度内测访问（云端权威） 字典数据初始化 SQL
-- 自动生成于: 2026-06-27 07:48:54.940414
-- =====================================================

-- 访问主体 字典类型
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES
('访问主体', 'hasn_subject_type', 'AI-Native 应用灰度内测访问（云端权威）模块-访问主体', NOW(), NULL)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, remark = EXCLUDED.remark, updated_time = NOW();

-- 访问主体 字典数据
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'hasn_subject_type' ORDER BY id DESC LIMIT 1;

    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'hasn_subject_type' AND value = 'owner') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('hasn_subject_type', '个人', 'owner', 'blue', 1, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'hasn_subject_type' AND value = 'enterprise') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('hasn_subject_type', '企业', 'enterprise', 'purple', 2, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
END $$;

-- 审批状态 字典类型
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES
('审批状态', 'hasn_status', 'AI-Native 应用灰度内测访问（云端权威）模块-审批状态', NOW(), NULL)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, remark = EXCLUDED.remark, updated_time = NOW();

-- 审批状态 字典数据
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'hasn_status' ORDER BY id DESC LIMIT 1;

    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'hasn_status' AND value = 'pending') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('hasn_status', '待审', 'pending', 'orange', 1, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'hasn_status' AND value = 'approved') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('hasn_status', '已通过', 'approved', 'green', 2, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'hasn_status' AND value = 'rejected') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('hasn_status', '已拒绝', 'rejected', 'red', 3, 1, v_dict_type_id, '', NOW(), NULL);
    END IF;
END $$;

-- =====================================================
-- 字典数据生成完成
-- =====================================================