-- =====================================================================================
-- 运营管理面 · 批次 0（T0.2）：清理无页面与应用面菜单
-- =====================================================================================
--
-- 【依据】
--   设计事实源：docs/产品与技术/技术设计/02-平台能力/运营管理面/实施/01-运营管理面建设施工清单.md
--                §1「已核实的基线」与「批次 0：清场」的 T0.1 / T0.2。
--   审计脚本  ：backend/scripts/ops_console/audit_menus.py（只读 dry-run，产出 A/B/C 三张表）
--   CI 守卫   ：backend/scripts/ops_console/check_menu_pages.py（断言每行 component 都能找到 .vue）
--
-- 【删除口径】三类，判断全部在审计脚本里做完，本文件只固化结论
--   A 无页面   ：component 归一化后拼一次 .vue，在前端 apps/web-antdv-next/src 的
--                views/ 与 plugins/ 双根下都找不到文件 —— 前端不做 /index 回落，这类菜单点开必 404。
--   B 应用面   ：路径命中 AI-Native 应用前缀（creator / hasn_copilot / hasn_design / hasn_growth /
--                hasn_imagelab / hasn_project / hasn_reel / lead_automation 等），
--                这些应用已移出平台运营面（施工清单 T0.3 已删对应 admin API 与前端页面）。
--   C 按表 CRUD：形如 /<模块>/<表名>/index 且表属于关系/社区类（hasn_community 模块下的逐表
--                管理页）。hasn_group_members / hasn_unread_counts 刻意不删——它们是批次 1
--                「内容安全」的读数据源，场景页建成前是运营看到这些数据的唯一入口。
--
--   ⚠️ SQL 里**不做**「找不到页面就删」这种判断 —— SQL 看不到前端文件。三类的 path 清单在下面
--      显式列出，改判据必须回审计脚本重跑，再重新生成本文件。
--
-- 【为什么按 path 删而不按 id 删】
--   sys_menu.id 是 SERIAL，开发库与生产库不一致；path 来自同一份种子 SQL（backend/sql/**/*menu*.sql
--   的幂等 DO $$ 块以 path 为查找键），跨环境稳定。实测本地库 768 行内 path 全表唯一、待删行 path 全部非空。
--   这是本文件能原样用于生产的前提。
--
-- 【删除范围】待删节点 + 其全部后代（递归 CTE）+ 因此变空的目录
--   1) 直接命中 A∪B∪C 去重后的菜单行；
--   2) 这些行的全部后代（主要是 type=2 按钮权限行），先删子孙再删父，不留孤儿；
--   3) 删除后**由本次删除导致**变空的 type=0 目录行（本来就空的目录不动）；
--   4) sys_role_menu 中指向被删菜单的关联行（该表无外键，不清就变悬挂引用）。
--
-- 【预期影响行数】2026-08-10 本地开发库 huanxing@127.0.0.1:15432 实测
--   sys_menu 执行前 768 行（目录 31、菜单 158、按钮 576、内嵌 1、外链 2）
--     直接命中          82 行（A 80 ∪ B 44 ∪ C 4，三类互有重叠：B ⊂ A，C 有 2 条同时属于 A）
--     连带后代         328 行（全部为 type=2 按钮权限行）
--     变空的目录        14 行（/creator /lead_automation /hasn_growth /hasn_knowledge /hasn_copilot
--                             /hasn_design /hasn_reel /hasn_memory /hasn_imagelab /hasn_project
--                             /notification /hasn_community /hasn_task /hasn_hosting）
--     合计删除         424 行 → 剩余 344 行
--     sys_role_menu     0 行（实测该表 56 行没有一行指向待删菜单）
--
--   ⚠️ 施工清单 §1 写的「删除后剩余 358」是 audit_menus.py 的 remaining 口径，**不含**第 3 步的
--      14 个空目录（脚本把它单列成「删除后会变成空目录的目录行：14」交人工确认）。本文件按 T0.2
--      的要求一并清掉空目录，故剩余是 344 = 358 − 14。两个数不矛盾，是口径差。
--
-- 【幂等】
--   重复执行安全：清单按 path 匹配，行已不在就匹配不到，DELETE 影响 0 行；空目录一步要求
--   「该目录有子节点在本次删除集合里」，重跑时删除集合为空，故不会误伤后续新建的空目录。
--
-- 【回滚】
--   执行前的全表备份（CSV，含表头，按 id 排序，已实测可完整还原：重灌回临时表后与原表 EXCEPT 双向 0 差异）：
--     /Users/mac/huanxing-backups/sys_menu/sys_menu_backup_20260810.csv        （768 行）
--     /Users/mac/huanxing-backups/sys_menu/sys_role_menu_backup_20260810.csv   （56 行）
--   还原（会覆盖当前内容，先确认没有他人新增菜单；\copy 不展开 ~，必须写绝对路径）：
--     BEGIN;
--     DELETE FROM sys_role_menu; DELETE FROM sys_menu;
--     \copy sys_menu FROM '/Users/mac/huanxing-backups/sys_menu/sys_menu_backup_20260810.csv' CSV HEADER
--     \copy sys_role_menu FROM '/Users/mac/huanxing-backups/sys_menu/sys_role_menu_backup_20260810.csv' CSV HEADER
--     SELECT setval('sys_menu_id_seq', (SELECT max(id) FROM sys_menu));
--     SELECT setval('sys_role_menu_id_seq', (SELECT max(id) FROM sys_role_menu));
--     COMMIT;
--   只回滚部分行时，从 CSV 里挑出对应 id 的行单独 INSERT 即可（id 列在 CSV 中原样保留）。
--   生产回滚请先在生产库自己做一份同样的 CSV 备份，本地这份的 id 与生产不通用。
--
-- 【配套动作】只删库行不删种子脚本，下次谁重跑种子菜单会长回来 ——
--   同批已删除 backend/sql/generated/ 下与上述 path 一一对应的 14 个 *_menu.sql 及其 8 个同名
--   *_dict.sql（agent_kb_grant / copilot_preference / copilot_session / document / document_version /
--   folder / hasn_cloud_node_events / hasn_cloud_nodes / hasn_group_members / hasn_imagelab_project /
--   hasn_node_authorization_codes / hasn_project_inspection / hasn_unread_counts / kb）。
--   其余 68 条待删 path 在 backend/sql/ 下已无任何种子文件（上一批 T0.3 删掉 38 个 *_menu.sql 时一并带走）。
--
--   ⚠️ 唯一残留：backend/sql/generated/hasn_all_menu.sql 的第 8、9 节（107–138 行）仍会重建
--      /hasn/hasn_group_members 与 /hasn/hasn_unread_counts。该文件另外 9 个菜单全部保留，
--      不能整文件删除；是否摘掉这两节取决于「C 类这 2 行到底删不删」的裁决，故本批未动它。
--
-- 【执行方式】
--   psql "postgresql://<user>@<host>:<port>/<db>" -v ON_ERROR_STOP=1 \
--        -f backend/sql/ops_console/2026-08-10-清理无页面与应用面菜单.sql
--   文件自带 BEGIN/COMMIT，整体原子；任一步报错即全部回滚。
-- =====================================================================================

BEGIN;

-- -------------------------------------------------------------------------------------
-- 第 1 步：固化待删清单（A / B / C 三类的 path 显式列出）
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE tmp_doomed_menu_path (
    path     varchar(200) NOT NULL,
    category char(1)      NOT NULL,          -- A / B / C，一条 path 可同时属于多类
    PRIMARY KEY (path, category)
) ON COMMIT DROP;

-- A 类 · 无页面：component 指向的 .vue 在前端 views/ 与 plugins/ 下都不存在，点开必 404（80 条）
INSERT INTO tmp_doomed_menu_path(path, category) VALUES
    ('/creator/hx_creator_account', 'A'),
    ('/creator/hx_creator_competitor', 'A'),
    ('/creator/hx_creator_content', 'A'),
    ('/creator/hx_creator_content_stage', 'A'),
    ('/creator/hx_creator_draft', 'A'),
    ('/creator/hx_creator_hot_topic', 'A'),
    ('/creator/hx_creator_media', 'A'),
    ('/creator/hx_creator_profile', 'A'),
    ('/creator/hx_creator_project', 'A'),
    ('/creator/hx_creator_publish', 'A'),
    ('/creator/hx_creator_topic', 'A'),
    ('/creator/hx_creator_viral_pattern', 'A'),
    ('/hasn/hasn_artifact_contributions', 'A'),
    ('/hasn/hasn_artifact_registration_outbox', 'A'),
    ('/hasn/hasn_artifacts', 'A'),
    ('/hasn/hasn_asset_bindings', 'A'),
    ('/hasn/hasn_content_translations', 'A'),
    ('/hasn/hasn_group_agent_invites', 'A'),
    ('/hasn/hasn_group_im_command_outbox', 'A'),
    ('/hasn/hasn_im_history_snapshot_conversations', 'A'),
    ('/hasn/hasn_im_history_snapshot_messages', 'A'),
    ('/hasn/hasn_im_history_snapshots', 'A'),
    ('/hasn/hasn_session_im_command_outbox', 'A'),
    ('/hasn/hasn_speech_catalog_release', 'A'),
    ('/hasn/hasn_speech_catalog_release_package', 'A'),
    ('/hasn/hasn_speech_package', 'A'),
    ('/hasn/hasn_storage_accounts', 'A'),
    ('/hasn/hasn_storage_entries', 'A'),
    ('/hasn/hasn_storage_export_items', 'A'),
    ('/hasn/hasn_storage_jobs', 'A'),
    ('/hasn/hasn_storage_migration_items', 'A'),
    ('/hasn/hasn_storage_objects', 'A'),
    ('/hasn/hasn_storage_reservations', 'A'),
    ('/hasn_community/hasn_doc_space_subscriptions', 'A'),
    ('/hasn_community/im_command_outbox', 'A'),
    ('/hasn_copilot/copilot_preference', 'A'),
    ('/hasn_copilot/copilot_session', 'A'),
    ('/hasn_copilot/meeting_minutes', 'A'),
    ('/hasn_copilot/meeting_transcript_segments', 'A'),
    ('/hasn_copilot/meetings', 'A'),
    ('/hasn_design/hasn_design_project', 'A'),
    ('/hasn_growth/activity', 'A'),
    ('/hasn_growth/customer', 'A'),
    ('/hasn_growth/form_submission', 'A'),
    ('/hasn_growth/growth_profile_suggestion', 'A'),
    ('/hasn_growth/growth_profile_version', 'A'),
    ('/hasn_growth/growth_project_migration_quarantine', 'A'),
    ('/hasn_growth/growth_review_suggestion', 'A'),
    ('/hasn_growth/opportunity', 'A'),
    ('/hasn_growth/optout_record', 'A'),
    ('/hasn_growth/outreach_message', 'A'),
    ('/hasn_growth/playbook', 'A'),
    ('/hasn_hosting/hasn_cloud_node_events', 'A'),
    ('/hasn_hosting/hasn_cloud_nodes', 'A'),
    ('/hasn_hosting/hasn_node_authorization_codes', 'A'),
    ('/hasn_imagelab/hasn_imagelab_project', 'A'),
    ('/hasn_knowledge/agent_kb_grant', 'A'),
    ('/hasn_knowledge/document', 'A'),
    ('/hasn_knowledge/document_version', 'A'),
    ('/hasn_knowledge/folder', 'A'),
    ('/hasn_knowledge/kb', 'A'),
    ('/hasn_memory/owner_profile_coverage', 'A'),
    ('/hasn_project/hasn_project', 'A'),
    ('/hasn_project/hasn_project_inspection', 'A'),
    ('/hasn_project/hasn_project_milestone', 'A'),
    ('/hasn_reel/reel_project', 'A'),
    ('/hasn_task/task_dispatch_outbox', 'A'),
    ('/lead_automation/lead_audit_log', 'A'),
    ('/lead_automation/lead_collection_job', 'A'),
    ('/lead_automation/lead_contact', 'A'),
    ('/lead_automation/lead_contact_source', 'A'),
    ('/lead_automation/lead_export_batch', 'A'),
    ('/lead_automation/lead_export_item', 'A'),
    ('/lead_automation/lead_firecrawl_request', 'A'),
    ('/lead_automation/lead_raw_record', 'A'),
    ('/lead_automation/lead_rejected_record', 'A'),
    ('/lead_automation/lead_source_config', 'A'),
    ('/marketplace/marketplace_agent_publish_request', 'A'),
    ('/marketplace/marketplace_personal_skill', 'A'),
    ('/notification/hasn_notification_im_command_outbox', 'A');

-- B 类 · 应用面：路径命中 AI-Native 应用前缀（这些应用已移出平台运营面）（44 条）
INSERT INTO tmp_doomed_menu_path(path, category) VALUES
    ('/creator/hx_creator_account', 'B'),
    ('/creator/hx_creator_competitor', 'B'),
    ('/creator/hx_creator_content', 'B'),
    ('/creator/hx_creator_content_stage', 'B'),
    ('/creator/hx_creator_draft', 'B'),
    ('/creator/hx_creator_hot_topic', 'B'),
    ('/creator/hx_creator_media', 'B'),
    ('/creator/hx_creator_profile', 'B'),
    ('/creator/hx_creator_project', 'B'),
    ('/creator/hx_creator_publish', 'B'),
    ('/creator/hx_creator_topic', 'B'),
    ('/creator/hx_creator_viral_pattern', 'B'),
    ('/hasn_copilot/copilot_preference', 'B'),
    ('/hasn_copilot/copilot_session', 'B'),
    ('/hasn_copilot/meeting_minutes', 'B'),
    ('/hasn_copilot/meeting_transcript_segments', 'B'),
    ('/hasn_copilot/meetings', 'B'),
    ('/hasn_design/hasn_design_project', 'B'),
    ('/hasn_growth/activity', 'B'),
    ('/hasn_growth/customer', 'B'),
    ('/hasn_growth/form_submission', 'B'),
    ('/hasn_growth/growth_profile_suggestion', 'B'),
    ('/hasn_growth/growth_profile_version', 'B'),
    ('/hasn_growth/growth_project_migration_quarantine', 'B'),
    ('/hasn_growth/growth_review_suggestion', 'B'),
    ('/hasn_growth/opportunity', 'B'),
    ('/hasn_growth/optout_record', 'B'),
    ('/hasn_growth/outreach_message', 'B'),
    ('/hasn_growth/playbook', 'B'),
    ('/hasn_imagelab/hasn_imagelab_project', 'B'),
    ('/hasn_project/hasn_project', 'B'),
    ('/hasn_project/hasn_project_inspection', 'B'),
    ('/hasn_project/hasn_project_milestone', 'B'),
    ('/hasn_reel/reel_project', 'B'),
    ('/lead_automation/lead_audit_log', 'B'),
    ('/lead_automation/lead_collection_job', 'B'),
    ('/lead_automation/lead_contact', 'B'),
    ('/lead_automation/lead_contact_source', 'B'),
    ('/lead_automation/lead_export_batch', 'B'),
    ('/lead_automation/lead_export_item', 'B'),
    ('/lead_automation/lead_firecrawl_request', 'B'),
    ('/lead_automation/lead_raw_record', 'B'),
    ('/lead_automation/lead_rejected_record', 'B'),
    ('/lead_automation/lead_source_config', 'B');

-- C 类 · 按表 CRUD：关系/社区类表的 /<模块>/<表名>/index 逐表管理页（4 条）
INSERT INTO tmp_doomed_menu_path(path, category) VALUES
    ('/hasn_community/hasn_doc_space_subscriptions', 'C'),
    ('/hasn_community/im_command_outbox', 'C');

-- -------------------------------------------------------------------------------------
-- 第 2 步：递归展开待删节点及其全部后代（含 type=2 按钮权限行）
--   CYCLE 子句是防御性的：parent_id 理论上不该成环，成环时 PostgreSQL 会截断而不是无限递归。
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE tmp_removed_menu_id (
    id     bigint  PRIMARY KEY,
    depth  integer NOT NULL,                 -- 0 = 直接命中清单，>0 = 后代层级
    direct boolean NOT NULL                  -- true = 直接命中，false = 连带删除
) ON COMMIT DROP;

WITH RECURSIVE tree AS (
    SELECT m.id, m.parent_id, 0 AS depth
    FROM sys_menu m
    WHERE m.path IN (SELECT DISTINCT path FROM tmp_doomed_menu_path)
    UNION ALL
    SELECT c.id, c.parent_id, t.depth + 1
    FROM sys_menu c
    JOIN tree t ON c.parent_id = t.id
) CYCLE id SET is_cycle USING cycle_path
INSERT INTO tmp_removed_menu_id (id, depth, direct)
SELECT id, min(depth), bool_or(depth = 0)
FROM tree
WHERE NOT is_cycle
GROUP BY id;

-- -------------------------------------------------------------------------------------
-- 第 3 步：算出「因本次删除而变空」的 type=0 目录
--   判据三条同时成立：① 自己不在删除集合里；② 至少有一个子节点在删除集合里（这才叫「因本次删除」，
--   本来就空的目录第二条不成立，因此不会被动到）；③ 删完之后一个子节点都不剩。
--   循环是为了处理「目录 A 的子目录 B 变空被删，A 随之变空」的级联；实测本地库 14 个空目录全是
--   顶层目录、无级联，一轮就收敛，循环只是通用性兜底（上限 10 轮防呆）。
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE tmp_empty_dir_id (
    id bigint PRIMARY KEY
) ON COMMIT DROP;

DO $$
DECLARE
    v_round    integer := 0;
    v_inserted integer;
BEGIN
    LOOP
        v_round := v_round + 1;
        EXIT WHEN v_round > 10;

        INSERT INTO tmp_empty_dir_id (id)
        SELECT d.id
        FROM sys_menu d
        WHERE d.type = 0
          AND NOT EXISTS (SELECT 1 FROM tmp_removed_menu_id r WHERE r.id = d.id)
          AND NOT EXISTS (SELECT 1 FROM tmp_empty_dir_id e WHERE e.id = d.id)
          -- ② 至少一个子节点被本次删除带走
          AND EXISTS (
              SELECT 1 FROM sys_menu c
              WHERE c.parent_id = d.id
                AND (EXISTS (SELECT 1 FROM tmp_removed_menu_id r WHERE r.id = c.id)
                     OR EXISTS (SELECT 1 FROM tmp_empty_dir_id e WHERE e.id = c.id))
          )
          -- ③ 删完之后没有任何幸存子节点
          AND NOT EXISTS (
              SELECT 1 FROM sys_menu c
              WHERE c.parent_id = d.id
                AND NOT EXISTS (SELECT 1 FROM tmp_removed_menu_id r WHERE r.id = c.id)
                AND NOT EXISTS (SELECT 1 FROM tmp_empty_dir_id e WHERE e.id = c.id)
          );

        GET DIAGNOSTICS v_inserted = ROW_COUNT;
        EXIT WHEN v_inserted = 0;
    END LOOP;
END $$;

-- 空目录并入删除集合（depth 给 -1，保证第 4 步按深度倒序时最后才删父目录）
INSERT INTO tmp_removed_menu_id (id, depth, direct)
SELECT id, -1, false FROM tmp_empty_dir_id
ON CONFLICT (id) DO NOTHING;

-- -------------------------------------------------------------------------------------
-- 第 4 步：打印本次影响面（执行日志里留痕，便于和审计脚本的数字对账）
-- -------------------------------------------------------------------------------------
DO $$
DECLARE
    v_total    integer;
    v_direct   integer;
    v_orphan   integer;
    v_empty    integer;
    v_role     integer;
    v_before   integer;
BEGIN
    SELECT count(*) INTO v_before FROM sys_menu;
    SELECT count(*) INTO v_total  FROM tmp_removed_menu_id;
    SELECT count(*) INTO v_direct FROM tmp_removed_menu_id WHERE direct;
    SELECT count(*) INTO v_orphan FROM tmp_removed_menu_id WHERE NOT direct AND depth > 0;
    SELECT count(*) INTO v_empty  FROM tmp_empty_dir_id;
    SELECT count(*) INTO v_role   FROM sys_role_menu rm WHERE rm.menu_id IN (SELECT id FROM tmp_removed_menu_id);

    RAISE NOTICE 'sys_menu 执行前 % 行；本次删除 % 行（直接命中 %、连带后代 %、变空目录 %），剩余 % 行；sys_role_menu 关联行 %',
        v_before, v_total, v_direct, v_orphan, v_empty, v_before - v_total, v_role;
END $$;

-- -------------------------------------------------------------------------------------
-- 第 5 步：先删关联，再按深度倒序删菜单（先子孙后父，不留孤儿）
--   sys_menu 当前没有 parent_id 自引用外键（本地实测 0 条 constraint 引用 sys_menu），
--   按深度倒序删是为了在「将来有人补上外键」时同样安全，也让执行顺序与 T0.2 的要求一致。
-- -------------------------------------------------------------------------------------
DELETE FROM sys_role_menu WHERE menu_id IN (SELECT id FROM tmp_removed_menu_id);

DO $$
DECLARE
    v_depth integer;
BEGIN
    FOR v_depth IN SELECT DISTINCT depth FROM tmp_removed_menu_id ORDER BY depth DESC LOOP
        DELETE FROM sys_menu WHERE id IN (SELECT id FROM tmp_removed_menu_id WHERE depth = v_depth);
    END LOOP;
END $$;

-- -------------------------------------------------------------------------------------
-- 第 6 步：收口自检 —— 删完不能留下 parent_id 悬挂的行，也不能留下悬挂的角色关联
-- -------------------------------------------------------------------------------------
DO $$
DECLARE
    v_dangling_menu integer;
    v_dangling_role integer;
BEGIN
    SELECT count(*) INTO v_dangling_menu
    FROM sys_menu c
    WHERE c.parent_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM sys_menu p WHERE p.id = c.parent_id);

    SELECT count(*) INTO v_dangling_role
    FROM sys_role_menu rm
    WHERE NOT EXISTS (SELECT 1 FROM sys_menu m WHERE m.id = rm.menu_id);

    IF v_dangling_menu > 0 OR v_dangling_role > 0 THEN
        RAISE EXCEPTION '清理后仍有悬挂引用：sys_menu 孤儿 % 行、sys_role_menu 悬挂 % 行，整体回滚',
            v_dangling_menu, v_dangling_role;
    END IF;

    RAISE NOTICE '自检通过：无 parent_id 孤儿、无悬挂角色菜单关联，sys_menu 剩余 % 行',
        (SELECT count(*) FROM sys_menu);
END $$;

COMMIT;
