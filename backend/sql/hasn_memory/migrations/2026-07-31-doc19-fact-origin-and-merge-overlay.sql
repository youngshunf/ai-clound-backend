-- =====================================================
-- doc19 S3 · 事实溯源三列 + 合并裁决 overlay + 主人画像合并态可见字段
-- =====================================================
-- 设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/归档/2026-08-06-旧记忆与知识库设计/旧域/19-多节点记忆分层与分身自治整理设计.md
--   §3.2 事实溯源三列 · §3.4 业务 status 与合并裁决 overlay 两层 · §4.6 主人第三类写者
--   · §5.5 主脑单点可见 · §8.2 增列汇总 · §11 切片 S3
--
-- 要点：
--   1. semantic_fact 增溯源三列（origin_kind / origin_node_id / origin_agent_id）+ 血缘
--      merged_from + 业务字段组 revision + 合并裁决 overlay 三列 + 时效 valid_until；
--   2. 溯源列组**不建外键**——`merged` / `retired` 等非实体取值不该撑在外键语义列上（§3.2）；
--   3. origin_agent_id 记录**全部主体类别**的写入分身（现表 agent_id 受
--      ck_semantic_fact_agent_id 约束仅 agent_self 可填，不能复用），本迁移不动那条约束；
--   4. 存量回填：origin_kind 由 DEFAULT 'node' 自动补齐，origin_node_id 留 NULL
--      表示「产自未知节点」——本地判自产片的判据是 origin_kind='node' 且
--      origin_node_id = 本节点 node_id，NULL 永不等于任何节点 id，因此不会被误判成自产片；
--   5. owner_memory 增 owner_edited（主人手工改过正文，下轮重算必须保留其意图，§4.6）
--      与最近一轮合并的可见性字段（§5.5 主人在记忆页看到「上次整理于 X，主脑在 <设备>」）。
--
-- 幂等：ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / 约束按 pg_constraint 判存，
--      可重复执行。
-- =====================================================

SET search_path TO hasn_memory, public;

-- ---------------------------------------------------------------
-- 一、semantic_fact：溯源三列 + 血缘 + revision（§3.2 / §8.2）
-- ---------------------------------------------------------------
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS origin_kind varchar(16) NOT NULL DEFAULT 'node';
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS origin_node_id varchar(64);
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS origin_agent_id varchar(40);
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS merged_from text NOT NULL DEFAULT '[]';
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;

-- ---------------------------------------------------------------
-- 二、semantic_fact：合并裁决 overlay 三列 + 时效 valid_until（§3.4 / §8.2）
-- ---------------------------------------------------------------
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS merge_verdict varchar(16);
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS merge_verdict_run varchar(40);
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS merge_judged_revision bigint;
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS valid_until bigint;

-- ---------------------------------------------------------------
-- 三、取值约束（幂等：先查 pg_constraint 再加）
-- ---------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_semantic_fact_origin_kind'
          AND conrelid = 'hasn_memory.semantic_fact'::regclass
    ) THEN
        ALTER TABLE hasn_memory.semantic_fact
            ADD CONSTRAINT ck_semantic_fact_origin_kind
            CHECK (origin_kind IN ('node', 'merged', 'retired'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_semantic_fact_merge_verdict'
          AND conrelid = 'hasn_memory.semantic_fact'::regclass
    ) THEN
        ALTER TABLE hasn_memory.semantic_fact
            ADD CONSTRAINT ck_semantic_fact_merge_verdict
            CHECK (merge_verdict IS NULL OR merge_verdict IN ('merged_into', 'disputed'));
    END IF;
END
$$;

-- ---------------------------------------------------------------
-- 四、索引
--   idx_semantic_fact_origin：本地/云端按「谁产的」筛自产片与退役片（§3.3 判据、purge 级联）
--   idx_semantic_fact_verdict：主人记忆页「待你确认」区与合并轮次复核（§5.4 第 6 条）
-- ---------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_semantic_fact_origin
    ON semantic_fact (owner_id, origin_kind, origin_node_id);
CREATE INDEX IF NOT EXISTS idx_semantic_fact_verdict
    ON semantic_fact (owner_id, status, merge_verdict);

-- ---------------------------------------------------------------
-- 五、列注释
-- ---------------------------------------------------------------
COMMENT ON COLUMN semantic_fact.origin_kind IS '溯源类别 (node:节点自产:blue/merged:合并派生:purple/retired:产生节点已退役:gray)';
COMMENT ON COLUMN semantic_fact.origin_node_id IS '产生节点 node_id（merged 时为空；节点退役后原值保留、只翻 origin_kind）';
COMMENT ON COLUMN semantic_fact.origin_agent_id IS '写入分身 hasn_id（全部主体类别都记；merged 时为空）';
COMMENT ON COLUMN semantic_fact.merged_from IS '被合并的 fact_id 数组 JSON（派生事实的血缘）';
COMMENT ON COLUMN semantic_fact.revision IS '业务字段组版本（每次变更 +1；上行幂等键成分、overlay 失效判据）';
COMMENT ON COLUMN semantic_fact.merge_verdict IS '合并裁决 (merged_into:已并入他条:purple/disputed:矛盾待主人确认:orange)';
COMMENT ON COLUMN semantic_fact.merge_verdict_run IS '作出该裁决的合并轮次 run_id';
COMMENT ON COLUMN semantic_fact.merge_judged_revision IS '裁决所依据的 revision（与当前 revision 不等即裁决过期作废）';
COMMENT ON COLUMN semantic_fact.valid_until IS '有效期截止 (epoch ms)；时效性事实，review 候选③依据';

-- ---------------------------------------------------------------
-- 六、owner_memory：主人手工编辑标记 + 最近一轮合并的可见性字段（§4.6 / §5.5）
-- ---------------------------------------------------------------
ALTER TABLE owner_memory ADD COLUMN IF NOT EXISTS owner_edited boolean NOT NULL DEFAULT false;
ALTER TABLE owner_memory ADD COLUMN IF NOT EXISTS last_merge_run_id varchar(40);
ALTER TABLE owner_memory ADD COLUMN IF NOT EXISTS last_merge_node_id varchar(64);
ALTER TABLE owner_memory ADD COLUMN IF NOT EXISTS last_merge_summary text;

COMMENT ON COLUMN owner_memory.owner_edited IS '主人是否手工改过正文（true 时下轮重算必须携带手工版本并保留其意图）';
COMMENT ON COLUMN owner_memory.last_merge_run_id IS '最近一轮合并的 run_id（merge_run.run_id）';
COMMENT ON COLUMN owner_memory.last_merge_node_id IS '最近一轮合并的执行节点 node_id（主脑所在设备）';
COMMENT ON COLUMN owner_memory.last_merge_summary IS '最近一轮合并的结果摘要（面向主人，记忆页可见）';
