-- 公共技能 revision 抖动根治（doc11 §5.5-3/§5.5-4，2026-07-02）：
-- 同一 skill/template 多条 is_latest=true 行（github_sync 写新版本历史上从不重置旧行）
-- + 快照子查询无 ORDER BY → PostgreSQL 挑行不定 → common_skills_revision 哈希横跳 →
-- 每 20 分钟全量 re-provision 风暴。本迁移：
--   1. 存量清洗：同 id 组内多条 is_latest=true 只保留 id 最大那条（与查询侧
--      DISTINCT ON ... ORDER BY id DESC 的确定性口径一致），其余置 false。
--      只动「is_latest=true 且组内确有多条」的行，单条 latest 的组不受影响。
--   2. partial unique index：写入期即拦重复，根治脏数据再生。
-- 幂等：UPDATE 天然幂等（第二次执行命中 0 行）；CREATE UNIQUE INDEX IF NOT EXISTS。
-- 表已搬入 hasn_marketplace schema（2026-06-14 迁移），须全限定。

-- 1a. 技能版本表存量清洗
UPDATE hasn_marketplace.marketplace_skill_version v
SET is_latest = false, updated_time = now()
WHERE v.is_latest
  AND v.id <> (
    SELECT max(v2.id)
    FROM hasn_marketplace.marketplace_skill_version v2
    WHERE v2.skill_id = v.skill_id AND v2.is_latest
  );

-- 1b. 模板版本表存量清洗（bundle 侧对称，评审 D3）
UPDATE hasn_marketplace.marketplace_template_version v
SET is_latest = false, updated_time = now()
WHERE v.is_latest
  AND v.id <> (
    SELECT max(v2.id)
    FROM hasn_marketplace.marketplace_template_version v2
    WHERE v2.template_id = v.template_id AND v2.is_latest
  );

-- 2. partial unique：每个 skill/template 至多一条 is_latest=true
CREATE UNIQUE INDEX IF NOT EXISTS uq_marketplace_skill_version_latest
  ON hasn_marketplace.marketplace_skill_version (skill_id) WHERE is_latest;

CREATE UNIQUE INDEX IF NOT EXISTS uq_marketplace_template_version_latest
  ON hasn_marketplace.marketplace_template_version (template_id) WHERE is_latest;
