-- 退役「场景专家人设」的 builtin 标记（2026-07-14）：对齐 2026-07-12「内置分身收敛为 3」的既定决策。
--
-- 背景：hub commit b48f7f1「P3-hub 下发两个内置工作流模板种子 + 补齐六个缺失人设」为支撑内置场景节点，
-- 把 6 个专家人设标成了 builtin=true，与 2026-07-12 福仔拍板「内置分身只留 assistant / content_operator /
-- analyst 三个」相矛盾——新用户 onboarding 的 reconcile_builtin_agents 会因此多建 6 个专家分身。
--
-- 纠正方向（与 2026-07-12-builtin-agents-converge-to-3.sql 同口径）：内置场景节点的专家角色改由
-- workflow_template 节点的 system_prompt 承载（专家=角色，不是分身）；这 6 个模板退为「可选市场模板」
-- （不再 builtin、模板本身保留、用户仍可手动创建）。hub yaml 已同步去 builtin，本迁移让存量库即时一致。
--
-- 安全：reconcile_builtin_agents 是 INSERT-only 且按 marketplace_template.builtin 过滤——去掉这 6 个模板的
-- builtin 标记后，新用户 onboarding 只建 3 个内置；已存在的分身（存量用户名下的专家分身）按各自
-- builtin_agent_key 独立保留，不随本迁移消失。
-- 幂等：仅在当前仍是 builtin 时改（多次执行不叠加副作用）。

UPDATE hasn_marketplace.marketplace_template
SET builtin = FALSE, builtin_key = NULL
WHERE template_id IN (
    'huanxing/agent/fullstack-engineer',
    'huanxing/agent/brand-designer',
    'huanxing/agent/market-researcher',
    'huanxing/agent/research-analyst',
    'huanxing/agent/quant-trader',
    'huanxing/agent/product-strategist'
);
