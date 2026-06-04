-- 公共技能加载机制（doc12 §3.2）：marketplace_skill 增 is_common 标记。
-- 公共技能 = is_common=true 且 status='published'，由 hub common-skills.yaml 经 webhook 同步打标。
-- 与 is_official 区分：official=唤星出品；common=精选子集，默认叠加进每个 Agent 技能清单。

ALTER TABLE marketplace_skill
    ADD COLUMN IF NOT EXISTS is_common BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN marketplace_skill.is_common IS '是否公共技能（默认叠加进每个 Agent 的技能清单）';

-- 公共技能集合解析高频读（按 is_common 过滤 published），加部分索引。
CREATE INDEX IF NOT EXISTS ix_marketplace_skill_is_common
    ON marketplace_skill (is_common)
    WHERE is_common;
