-- 工作流场景领域字典补 seed：career（职业发展）。
--
-- 背景：hub 新增内置工作流模板「职业优化」（hasn-hub/workflow-templates/career-optimization，
-- template_key=career_optimization），其 domain=career。模板规范（README §字段规范）与
-- workflow_template_service.build_builtin_template_data 的注释口径一致：**domain 须已在
-- 系统字典 workflow_template_domain 内，发布链路不新建**——字典缺 career 时模板虽能入库，
-- 但画廊领域分组拿不到显示元数据（组名/图标/色），场景会落出分组之外。
--
-- 本迁移只补一条 sys_dict_data：career = 职业发展（color=amber，icon=briefcase 存 remark，
-- sort=5 排在 professional 之后）。字典类型行（sys_dict_type）由 2026-07-14-workflow-template.sql
-- 建过，这里直接按 code 取；取不到时显式 RAISE——那意味着基线迁移没跑过，不该静默吞掉。
--
-- accent 对齐 doc94 §8.4：webui scenarioVisuals.ts 的 ScenarioAccent 枚举已含 amber，
-- 模板侧 accent=amber 与字典 color=amber 同词，无需前端改动。
--
-- 幂等：重复执行时 IF NOT EXISTS 守卫命中，影响 0 行。
DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'workflow_template_domain' ORDER BY id DESC LIMIT 1;

    IF v_dict_type_id IS NULL THEN
        RAISE EXCEPTION 'sys_dict_type 缺少 workflow_template_domain（基线迁移 2026-07-14-workflow-template.sql 未应用）';
    END IF;

    -- career 职业发展（accent amber / icon briefcase）
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'workflow_template_domain' AND value = 'career') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('workflow_template_domain', '职业发展', 'career', 'amber', 5, 1, v_dict_type_id, 'briefcase', NOW(), NULL);
    END IF;
END $$;
