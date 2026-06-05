-- 技能包重新设计（实施/91 B2.5）：hasn_agents 增 skill_bundles 列，记录已安装 skill_pack 引用。
-- 形态：JSONB 数组 [{template_id, version}]。attach_bundle_cloud_first 安装时 append（按 template_id
-- 去重），get_agent_profile 据此 join marketplace_template_version 输出 skill_bundles（hermes-runtime
-- 据此物化 skill-bundles/*.yaml）。成员技能仍并入 hasn_agents.skills（与 attach_skill 一致）。

ALTER TABLE public.hasn_agents
    ADD COLUMN IF NOT EXISTS skill_bundles JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.hasn_agents.skill_bundles IS '已安装技能包引用 [{template_id, version}]（成员技能另并入 skills）';
