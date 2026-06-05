-- 技能包重新设计（实施/91 B3.1）：marketplace_template 增 is_common 标记。
-- 公共技能包 = template_type='skill_pack' 且 is_common=true 且 status='published'，由 hub
-- bundles/ 目录约定经 webhook 同步打标（_sync_bundle）。与 is_official 区分：official=唤星出品；
-- common=精选子集，默认叠加进每个 Agent 的能力清单（与 marketplace_skill.is_common 对称）。

ALTER TABLE public.marketplace_template
    ADD COLUMN IF NOT EXISTS is_common BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.marketplace_template.is_common IS '是否公共技能包（默认叠加进每个 Agent 的能力清单）';

-- 公共技能包集合解析高频读（按 is_common 过滤），加部分索引。
CREATE INDEX IF NOT EXISTS ix_marketplace_template_is_common
    ON public.marketplace_template (is_common)
    WHERE is_common;
