-- 修复历史技能包把发布制品 manifest/file 哈希误写进 definition content_hash 的数据。
-- 仅当 Agent 冻结引用仍等于版本行的旧哈希时才改写；真实的旧 definition 哈希保留为 drift，
-- 禁止用当前定义覆盖用户已经冻结的历史内容。
WITH definitions AS (
    SELECT
        version_row.template_id,
        version_row.version,
        NULLIF(btrim(COALESCE(version_row.content_hash, version_row.file_hash, '')), '') AS legacy_hash,
        'sha256:' || encode(sha256(convert_to(version_row.hermes_yaml, 'UTF8')), 'hex') AS definition_hash
    FROM hasn_marketplace.marketplace_template_version AS version_row
    JOIN hasn_marketplace.marketplace_template AS template_row
      ON template_row.template_id = version_row.template_id
    WHERE template_row.template_type = 'skill_pack'
      AND NULLIF(version_row.hermes_yaml, '') IS NOT NULL
), rewritten_agents AS (
    SELECT
        agent.id AS agent_id,
        jsonb_agg(
            CASE
                WHEN definition.template_id IS NOT NULL
                     AND definition.legacy_hash IS NOT NULL
                     AND bundle.value ->> 'content_hash' = definition.legacy_hash
                     AND definition.legacy_hash IS DISTINCT FROM definition.definition_hash
                THEN jsonb_set(
                    bundle.value,
                    '{content_hash}',
                    to_jsonb(definition.definition_hash),
                    true
                )
                ELSE bundle.value
            END
            ORDER BY bundle.ordinality
        ) AS skill_bundles
    FROM public.hasn_agents AS agent
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(agent.skill_bundles) = 'array' THEN agent.skill_bundles
            ELSE '[]'::jsonb
        END
    ) WITH ORDINALITY AS bundle(value, ordinality)
    LEFT JOIN definitions AS definition
      ON definition.template_id = COALESCE(
          bundle.value ->> 'package_id',
          bundle.value ->> 'template_id'
      )
     AND definition.version = bundle.value ->> 'version'
    GROUP BY agent.id
)
UPDATE public.hasn_agents AS agent
SET skill_bundles = rewritten.skill_bundles,
    profile_revision = COALESCE(agent.profile_revision, 1) + 1,
    updated_time = now()
FROM rewritten_agents AS rewritten
WHERE agent.id = rewritten.agent_id
  AND agent.skill_bundles IS DISTINCT FROM rewritten.skill_bundles;

-- statement-breakpoint

UPDATE hasn_marketplace.marketplace_template_version AS version_row
SET content_hash = 'sha256:' || encode(sha256(convert_to(version_row.hermes_yaml, 'UTF8')), 'hex'),
    updated_time = now()
FROM hasn_marketplace.marketplace_template AS template_row
WHERE template_row.template_id = version_row.template_id
  AND template_row.template_type = 'skill_pack'
  AND NULLIF(version_row.hermes_yaml, '') IS NOT NULL
  AND version_row.content_hash IS DISTINCT FROM (
      'sha256:' || encode(sha256(convert_to(version_row.hermes_yaml, 'UTF8')), 'hex')
  );

-- statement-breakpoint

COMMENT ON COLUMN hasn_marketplace.marketplace_template_version.content_hash IS
    '内容哈希；skill_pack 恒为 sha256(Hermes definition UTF-8 字节)，发布制品哈希只写 file_hash';
