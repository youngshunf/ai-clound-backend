-- APPDEMO-1：hasn_app_catalog.release_phase 增加 `demo`（演示）阶段。
--
-- 背景（福仔 2026-08-16）：Demo 版本不再演进新功能，但应用中心里有一批应用功能未闭环。
-- 它们既不该下架（下架等于入口全消失、外部演示时看不到产品面貌），也不该继续放出工具
-- （分身搜得到却调不动，或调用后写出半成品数据）。故新增一个与上架状态正交的发布阶段：
--
--   demo = 应用中心照常可见可点开，页面渲染的是**静态高保真原型稿**（示例数据、不可真实操作），
--          且该应用的**全部工具对分身隐身**（搜索与执行两面都不可见）。
--
-- 与既有三档的关系（release_phase 与 status 正交这条不变）：
--   ga         正式发布——真实功能，工具正常
--   beta_full  全量内测——真实功能，工具正常（内测的用途就是连分身一起真实试用）
--   beta_gray  灰度内测——同上，仅可见范围收窄到获批用户
--   demo       演示稿——原型页 + 工具隐身   ← 本次新增
--
-- 判定源在 `app_catalog_service.tools_hidden_for_phase`，经 access['tools_hidden'] 下发给
-- 云端网关与本地 hasn-mcp 两个工具面消费；本迁移只负责让 `demo` 成为该列的合法取值并让
-- 管理端字典/codegen 认得它。列类型 varchar(16) 容得下，无需改类型。
--
-- 幂等：只改 COMMENT 与补字典行，可重复执行。

COMMENT ON COLUMN "public"."hasn_app_catalog"."release_phase" IS
    '发布阶段 (ga:正式:green/beta_full:全量内测:blue/beta_gray:灰度内测:orange/demo:演示:purple)';

-- 管理端「发布阶段」下拉走本地常量（见 hasn-cloud-frontend-demo 的 hasn_app_catalog/data.ts，
-- 那里刻意不取共享字典 hasn_status——它混了约 50 个别的模块的状态值）。此处补的字典行只服务
-- 列表页的 CellTag 兜底与 codegen，不构成下拉数据源。
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES ('发布阶段', 'hasn_release_phase', 'AI-Native 应用目录发布阶段（与上架状态正交）', NOW(), NULL)
ON CONFLICT (code) DO NOTHING;

DO $$
DECLARE
    v_phase_id INTEGER;
BEGIN
    SELECT id INTO v_phase_id FROM sys_dict_type WHERE code = 'hasn_release_phase' ORDER BY id LIMIT 1;

    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_release_phase' AND value='ga') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_release_phase','正式发布 (GA)','ga','green',1,1,v_phase_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_release_phase' AND value='beta_full') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_release_phase','全量内测','beta_full','blue',2,1,v_phase_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_release_phase' AND value='beta_gray') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_release_phase','灰度内测','beta_gray','orange',3,1,v_phase_id,'',NOW(),NULL); END IF;
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code='hasn_release_phase' AND value='demo') THEN
        INSERT INTO sys_dict_data (type_code,label,value,color,sort,status,type_id,remark,created_time,updated_time)
        VALUES ('hasn_release_phase','演示（原型稿·分身无工具）','demo','purple',4,1,v_phase_id,'',NOW(),NULL); END IF;
END $$;
