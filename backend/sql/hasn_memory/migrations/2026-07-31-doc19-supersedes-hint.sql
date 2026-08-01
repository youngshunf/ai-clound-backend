-- =====================================================
-- doc19 S2 · semantic_fact 增 supersedes_hint 正式列（本人纠正快速通道指向）
-- =====================================================
-- 设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md
--   §4.3 分身遇到「别处的事实是错的」怎么办 · §7.1 save 入参 · §8.2 增列汇总（双端一致）
--   · 决策 D-21 save(supersedes_hint) 本人纠正快速通道
--
-- 要点：
--   1. `supersedes_hint` 是 save 携带的**线索**，不是已生效的取代关系——与 `superseded_by`
--      语义严格区分：后者表示「已经被取代了」，前者只表示「写这条的分身认为那条该被取代」，
--      是否生效由 S6 合并的规则层裁决（hint 指向的旧事实 origin_agent_id 与新事实一致 =
--      本人纠正本人 → 规则层直接按 hint 裁决，不调 LLM；指向他人事实只作 LLM 参考信号）；
--   2. 列组**不建外键**：hint 可能指向另一节点上、本库尚未汇聚到的 fact_id，也可能指向已被
--      主人 purge 的事实（§4.5 墓碑），外键会让正当的写入直接失败；
--   3. 宽度 varchar(40) 与 semantic_fact.fact_id / superseded_by 同宽，双端一致（本地 crate
--      也是 40）；
--   4. S3 的临时落法（把 hint 塞进 source_refs_json 的结构化条目）在本迁移后**收敛为正式列**
--      ——§8.2 增列汇总本就把它列为双端增列，塞 JSON 串检索不到、合并规则层没法按列取。
--      存量回填见下方第二节。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + 回填语句自带「仅当新列为空且 JSON 串里确有 hint」判据，
--      可重复执行。
-- =====================================================

SET search_path TO hasn_memory, public;

-- ---------------------------------------------------------------
-- 一、增列（§4.3 / §8.2）
-- ---------------------------------------------------------------
ALTER TABLE semantic_fact ADD COLUMN IF NOT EXISTS supersedes_hint varchar(40);

COMMENT ON COLUMN semantic_fact.supersedes_hint IS
    '本人纠正快速通道指向的旧 fact_id（doc19 §4.3）；只是线索、非已生效取代关系，由合并规则层裁决；不建外键（可指向本库尚未汇聚或已 purge 的事实）';

-- ---------------------------------------------------------------
-- 二、存量回填：把 S3 临时塞进 source_refs_json 的 hint 提到正式列
--   source_refs_json 形如 [..., {"kind": "supersedes_hint", "fact_id": "<id>"}]，
--   取第一条 kind='supersedes_hint' 的 fact_id。仅回填新列尚空的行，重复执行不改已回填值。
-- ---------------------------------------------------------------
UPDATE semantic_fact AS f
SET supersedes_hint = h.fact_id
FROM (
    SELECT
        sf.fact_id AS row_fact_id,
        (
            SELECT elem ->> 'fact_id'
            FROM jsonb_array_elements(sf.source_refs_json::jsonb) AS elem
            WHERE elem ->> 'kind' = 'supersedes_hint'
              AND coalesce(elem ->> 'fact_id', '') <> ''
            LIMIT 1
        ) AS fact_id
    FROM semantic_fact AS sf
    WHERE sf.supersedes_hint IS NULL
      AND sf.source_refs_json LIKE '%supersedes_hint%'
      -- 历史脏数据可能不是合法 JSON 数组，先挡掉再解析，避免整条迁移崩在一行上
      AND jsonb_typeof(
          CASE WHEN sf.source_refs_json ~ '^\s*\[' THEN sf.source_refs_json::jsonb ELSE '0'::jsonb END
      ) = 'array'
) AS h
WHERE f.fact_id = h.row_fact_id
  AND h.fact_id IS NOT NULL
  AND f.supersedes_hint IS NULL;

-- ---------------------------------------------------------------
-- 三、索引：合并规则层按 hint 反查「谁想取代我」（§5.4 规则去重第一层）
--   仅索引非空行——绝大多数事实不带 hint，部分索引省一大截空间
-- ---------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_semantic_fact_supersedes_hint
    ON semantic_fact (owner_id, supersedes_hint)
    WHERE supersedes_hint IS NOT NULL;
