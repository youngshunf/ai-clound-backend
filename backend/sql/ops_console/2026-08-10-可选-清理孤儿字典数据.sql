-- =====================================================================================
-- 运营管理面 · 批次 0 尾巴：清理孤儿字典数据（**可选 · 默认不执行**）
-- =====================================================================================
--
-- 【这是什么】
--   2026-08-10 删掉了 backend/sql/generated/ 下 28 个**没有配对 *_menu.sql** 的 *_dict.sql 种子文件
--   （对应的 AI-Native 应用 admin 面已在前几批随菜单一并删除）。删种子只保证「以后不会再种回来」，
--   **不会**动库里已经种下去的 sys_dict_type / sys_dict_data 行 —— 那是运行态数据，清不清是另一个决定。
--
--   本文件把「清掉那些运行态行」这个动作写成可执行 SQL 备查。**它没有在任何环境执行过。**
--
-- 【为什么默认不执行】
--   删库行的唯一收益是让字典管理页少 15 个没人用的字典类型；代价是一旦将来某个应用面被恢复，
--   标签会全部变空。收益远小于代价，所以默认留着。真要清，按下面的前置条件逐条核过再跑。
--
-- 【清理范围 —— 15 个字典码，判据是「种子文件删完后已无任何文件定义它」】
--   核对方法（在 backend/sql/generated/ 下跑，输出必须为空才说明它确实没有种子了）：
--     grep -l "'<code>'" *.sql
--
--   hasn_growth_lifecycle_status      原 customer_dict.sql
--   hasn_growth_status                原 form_submission_dict.sql / outreach_message_dict.sql
--   hasn_design_status                原 hasn_design_project_dict.sql
--   hasn_project_status               原 hasn_project_dict.sql / hasn_project_milestone_dict.sql
--   creator_status                    原 hx_creator_content / _content_stage / _publish / _topic_dict.sql
--   creator_type                      原 hx_creator_media_dict.sql
--   creator_category                  原 hx_creator_viral_pattern_dict.sql
--   lead_automation_event_type        原 lead_audit_log_dict.sql
--   lead_automation_source_types      原 lead_collection_job_dict.sql
--   lead_automation_status            原 lead_collection_job / lead_contact / lead_export_batch /
--                                        lead_firecrawl_request / lead_raw_record_dict.sql
--   lead_automation_source_type       原 lead_contact / lead_contact_source / lead_firecrawl_request /
--                                        lead_raw_record / lead_rejected_record / lead_source_config_dict.sql
--   lead_automation_response_status   原 lead_firecrawl_request_dict.sql
--   hasn_reel_status                  原 reel_creation_dict.sql / reel_project_dict.sql
--   hasn_studio_origin_type           原 studio_artifact_dict.sql
--   hasn_studio_status                原 studio_project / studio_render_job / studio_artifact_dict.sql
--
-- 【刻意不在清理范围内的 7 个码 —— 删了会让保留下来的页面标签变空】
--   user_tier_reference_type / user_tier_transaction_type / user_tier_credit_type / user_tier_source_type
--     前端 apps/web-antdv-next/src/views/user_tier/credit_transaction/data.ts 与
--     .../user_tier/user_credit_balance/data.ts 仍在 getDictOptions() 里用它们，页面未删、菜单未删。
--     种子文件 credit_transaction_dict.sql / user_credit_balance_dict.sql / official_grant_dict.sql
--     同批保留，本文件也不碰对应的库行。
--   marketplace_category
--     种子仍由 marketplace_skill_dict.sql / marketplace_template_dict.sql 提供（两者都有配对 menu），
--     前端 views/marketplace/marketplace_skill|marketplace_template 在用。被删的
--     marketplace_personal_skill_dict.sql 只是同码同值的第三份重复定义，删它对库无影响。
--   creator_auth_status / creator_source_type
--     种子仍由 creator_dict_fixed.sql 提供（该文件不叫 *_dict.sql，不在本批清理口径内）。
--
-- 【预期影响行数】2026-08-10 本地开发库 huanxing@127.0.0.1:15432 只读实测
--   sys_dict_type 136 行 / sys_dict_data 619 行
--   15 个码里只有 10 个真的种进过本地库：
--     hasn_design_status 3 / hasn_growth_lifecycle_status 7 / hasn_growth_status 17 /
--     hasn_project_status 8 / hasn_reel_status 2 / lead_automation_event_type 2 /
--     lead_automation_response_status 2 / lead_automation_source_type 2 /
--     lead_automation_source_types 2 / lead_automation_status 2
--   合计 sys_dict_data 47 行、sys_dict_type 10 行。
--   ⚠️ 生产库行数**必然不同**（种子执行历史不一样），必须先跑本文件顶部的只读审计再决定。
--
-- 【执行前必做】
--   1. 跑「第 0 步 只读审计」，人工看清单；
--   2. 备份两张表（CSV，绝对路径，\copy 不展开 ~）：
--        \copy sys_dict_type TO '<绝对路径>/sys_dict_type_backup_<日期>.csv' CSV HEADER
--        \copy sys_dict_data TO '<绝对路径>/sys_dict_data_backup_<日期>.csv' CSV HEADER
--   3. 确认没有别的分支在制新页面要用这些码（grep 两个仓的 getDictOptions）。
--
-- 【回滚】从上面的 CSV 挑出对应 type_code 的行 INSERT 回去即可（id 列在 CSV 中原样保留）。
--
-- 【执行方式】默认注释掉了 DML，要真跑请手工去掉 “第 2 步” 里两条 DELETE 前的注释符。
--   psql "postgresql://<user>@<host>:<port>/<db>" -v ON_ERROR_STOP=1 \
--        -f backend/sql/ops_console/2026-08-10-可选-清理孤儿字典数据.sql
-- =====================================================================================

BEGIN;

-- -------------------------------------------------------------------------------------
-- 第 0 步：只读审计 —— 不去掉下面 DELETE 的注释时，整个文件就只是这一段，安全可反复跑
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE tmp_orphan_dict_code (code varchar(32) PRIMARY KEY) ON COMMIT DROP;

INSERT INTO tmp_orphan_dict_code(code) VALUES
    ('hasn_growth_lifecycle_status'),
    ('hasn_growth_status'),
    ('hasn_design_status'),
    ('hasn_project_status'),
    ('creator_status'),
    ('creator_type'),
    ('creator_category'),
    ('lead_automation_event_type'),
    ('lead_automation_source_types'),
    ('lead_automation_status'),
    ('lead_automation_source_type'),
    ('lead_automation_response_status'),
    ('hasn_reel_status'),
    ('hasn_studio_origin_type'),
    ('hasn_studio_status');

-- 待删的字典类型（本环境实际存在的那些）
SELECT t.id, t.code, t.name, t.remark
FROM sys_dict_type t
JOIN tmp_orphan_dict_code o ON o.code = t.code
ORDER BY t.code;

-- 待删的字典数据明细
SELECT d.type_code, d.value, d.label, d.status
FROM sys_dict_data d
JOIN tmp_orphan_dict_code o ON o.code = d.type_code
ORDER BY d.type_code, d.sort, d.value;

-- 汇总，和上面「预期影响行数」对账
DO $$
DECLARE
    v_type_all  integer;
    v_data_all  integer;
    v_type_hit  integer;
    v_data_hit  integer;
BEGIN
    SELECT count(*) INTO v_type_all FROM sys_dict_type;
    SELECT count(*) INTO v_data_all FROM sys_dict_data;
    SELECT count(*) INTO v_type_hit FROM sys_dict_type t JOIN tmp_orphan_dict_code o ON o.code = t.code;
    SELECT count(*) INTO v_data_hit FROM sys_dict_data d JOIN tmp_orphan_dict_code o ON o.code = d.type_code;

    RAISE NOTICE 'sys_dict_type 全表 % 行，命中待删 % 行，剩余 %；sys_dict_data 全表 % 行，命中待删 % 行，剩余 %',
        v_type_all, v_type_hit, v_type_all - v_type_hit,
        v_data_all, v_data_hit, v_data_all - v_data_hit;
END $$;

-- -------------------------------------------------------------------------------------
-- 第 1 步：护栏 —— 命中数为 0 说明本环境根本没种过，直接当作无需清理（不报错，方便幂等重跑）
-- -------------------------------------------------------------------------------------
DO $$
DECLARE
    v_hit integer;
BEGIN
    SELECT count(*) INTO v_hit
    FROM sys_dict_type t JOIN tmp_orphan_dict_code o ON o.code = t.code;

    IF v_hit = 0 THEN
        RAISE NOTICE '本环境没有任何待删字典类型，无需清理（可能已清过，或从未种过这些码）';
    END IF;
END $$;

-- -------------------------------------------------------------------------------------
-- 第 2 步：真正的删除 —— **默认注释掉**。确认过第 0 步清单并已备份后再手工放开这两条。
--   顺序必须是先 data 后 type：sys_dict_data.type_id 指向 sys_dict_type.id，
--   本地实测该列没有外键约束，但反过来删会留下无主的数据行。
-- -------------------------------------------------------------------------------------
-- DELETE FROM sys_dict_data
--  WHERE type_code IN (SELECT code FROM tmp_orphan_dict_code);
--
-- DELETE FROM sys_dict_type
--  WHERE code IN (SELECT code FROM tmp_orphan_dict_code);

-- -------------------------------------------------------------------------------------
-- 第 3 步：收口自检 —— 放开第 2 步后，不允许留下 type_code 无主的字典数据行
--   （只检查本次涉及的码；全表历史脏数据不在本批范围内，避免把别人的问题算到这次头上）
-- -------------------------------------------------------------------------------------
DO $$
DECLARE
    v_dangling integer;
BEGIN
    SELECT count(*) INTO v_dangling
    FROM sys_dict_data d
    JOIN tmp_orphan_dict_code o ON o.code = d.type_code
    WHERE NOT EXISTS (SELECT 1 FROM sys_dict_type t WHERE t.code = d.type_code);

    IF v_dangling > 0 THEN
        RAISE EXCEPTION '清理后仍有 % 行字典数据的 type_code 已无对应类型，整体回滚', v_dangling;
    END IF;
END $$;

COMMIT;
