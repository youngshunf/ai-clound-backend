-- 工作流节点技能包绑定：给 hasn_task.workflow_node 补 skill_bundle_ids 列并回填存量。
--
-- 背景：节点技能面本来就是两个正交集合——单技能（skills）与技能包（skill_bundle_ids）。
-- 建图入参 WorkflowNodeSpec 早就有 skill_bundle_ids，创建节点时也确实写进了节点的 task 投影行
-- （hasn_task.task.skill_bundle_ids），但**专属表 workflow_node 从来没有这一列**。
-- 而读侧 WorkflowService.get_workflow 是「优先读 workflow_node 专属表」，
-- definition_snapshot 又是从它重组 graph_snapshot 下发给 daemon 的——
-- 于是技能包在「模板 → 云端 → 节点派发」链路的这一站被整段丢掉，
-- 与 doc35 B1 修掉的 output_spec / apps / is_origin / display 是同一类死列。
--
-- 列名取 skill_bundle_ids（不是 skill_bundles）：同一业务字段在 hub 模板、云端 task 表、
-- 云端建图入参、节点 LocalTaskRecord 上必须同名，链路上只允许新增或减少、禁止改名转换。
--
-- 回填口径：按 workflow.template_key 找到来源模板，再按 node_key 从模板 graph_spec 取
-- skill_bundle_ids 写入；只动仍是空数组的行（主人经「编辑链路」写过的实例私有定义不覆盖）。
-- 模板已删 / node_key 对不上（模板改版改名）的行保持空值，不臆造技能。
-- 幂等：重复执行时已回填行不再命中 WHERE 条件，影响 0 行。
--
-- ⚠️ 执行顺序：回填的数据源是 workflow_template.graph_spec，而模板里的 skill_bundle_ids 由
-- hub 同步写入。**必须先让 hub 的 workflow-templates 同步上来，再跑本迁移的 UPDATE**，
-- 否则取到的全是 NULL、一行也回填不到（本迁移在本机 PG 首跑即 UPDATE 0，正是这个原因）。
-- 本迁移幂等，同步完成后原样重跑即可补上；ALTER 部分先跑不受影响。
--
-- ⚠️ 覆盖范围：本迁移只修云端。**已经实例化并镜像到 daemon 本地的存量场景实例拿不到技能**——
-- 本地 workflow_nodes 是实例化那一刻写死的镜像，云端回填不会回流。存量实例要么重新起一个，
-- 要么单独做一次本地重镜像；新建实例走完整链路，无此问题。
ALTER TABLE hasn_task.workflow_node
    ADD COLUMN IF NOT EXISTS skill_bundle_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN hasn_task.workflow_node.skill_bundle_ids IS '默认技能包绑定 [bundle_slug...]';

UPDATE hasn_task.workflow_node wn
SET skill_bundle_ids = tpl_node.spec -> 'skill_bundle_ids'
FROM hasn_task.workflow w
JOIN hasn_task.workflow_template t ON t.template_key = w.template_key
CROSS JOIN LATERAL jsonb_array_elements(t.graph_spec -> 'nodes') AS tpl_node(spec)
WHERE wn.workflow_uuid = w.workflow_uuid
  AND wn.node_key = tpl_node.spec ->> 'node_key'
  AND jsonb_typeof(tpl_node.spec -> 'skill_bundle_ids') = 'array'
  AND (wn.skill_bundle_ids IS NULL OR wn.skill_bundle_ids = '[]'::jsonb);
