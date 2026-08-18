-- 回填场景实例节点的呈现元数据 display（{order, step_label}）。
--
-- 背景：场景实例化管道（workflow_template_service._template_to_workflow_params 与
-- workflow_service.create_workflow）曾把模板 graph_spec.nodes[].display 整段丢弃——
-- 建图入参 WorkflowNodeSpec 没有该字段、建图写入硬编码 display='{}'::jsonb。
-- 后果：全部场景实例节点行 display 恒空，端侧链路图只能按 node_key 字母序兜底编号
-- （一人公司链路上「市场调研 research」被排成第 8 环、落在「产品研发 product」之后），
-- 环号/阶段卡顺序/「来自 ②」来源标签全部错乱。
--
-- 回填口径：按 workflow.template_key 找到来源模板，再按 node_key 从模板 graph_spec 取回
-- display 写入；只动 display 为 NULL 或空对象的行——主人经「编辑链路」写过的实例私有
-- 定义不覆盖。模板已删 / node_key 对不上（模板改版改名）的行保持原值，不臆造顺序。
-- 幂等：重复执行时已回填行不再命中 WHERE 条件，影响 0 行。
UPDATE hasn_task.workflow_node wn
SET display = tpl_node.spec -> 'display'
FROM hasn_task.workflow w
JOIN hasn_task.workflow_template t ON t.template_key = w.template_key
CROSS JOIN LATERAL jsonb_array_elements(t.graph_spec -> 'nodes') AS tpl_node(spec)
WHERE wn.workflow_uuid = w.workflow_uuid
  AND wn.node_key = tpl_node.spec ->> 'node_key'
  AND tpl_node.spec -> 'display' IS NOT NULL
  AND (wn.display IS NULL OR wn.display = '{}'::jsonb);
