-- 技能市场本地工具面切换：规范化个人技能引用，并把存量技能包引用冻结到不可变版本。
-- 个人技能必须先按 owner 私有库反查；其余存量项保守留作 direct，保证迁移前后有效集合不减少。
WITH agent_skill_rows AS (
    SELECT
        agent.id AS agent_id,
        agent.owner_id,
        owner.user_id AS owner_user_id,
        item.ordinality,
        CASE
            WHEN jsonb_typeof(item.value) = 'string' THEN item.value #>> '{}'
            WHEN jsonb_typeof(item.value) = 'object'
                THEN COALESCE(item.value ->> 'skill_id', item.value ->> 'id')
            ELSE NULL
        END AS stored_skill_id
    FROM public.hasn_agents AS agent
    LEFT JOIN public.hasn_humans AS owner ON owner.hasn_id = agent.owner_id
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(agent.skills) = 'array' THEN agent.skills
            WHEN jsonb_typeof(agent.skills) = 'object'
                 AND jsonb_typeof(agent.skills -> 'enabled') = 'array'
                THEN agent.skills -> 'enabled'
            WHEN jsonb_typeof(agent.skills) = 'object' THEN (
                SELECT COALESCE(jsonb_agg(to_jsonb(entry.key)), '[]'::jsonb)
                FROM jsonb_object_keys(agent.skills) AS entry(key)
            )
            ELSE '[]'::jsonb
        END
    ) WITH ORDINALITY AS item(value, ordinality)
    WHERE agent.skills IS NOT NULL
), canonical_skill_rows AS (
    SELECT
        row.agent_id,
        row.ordinality,
        COALESCE(
            (
                SELECT personal.personal_skill_id
                FROM hasn_marketplace.marketplace_personal_skill AS personal
                WHERE (
                    personal.hasn_id = row.owner_id
                    OR personal.user_id = row.owner_user_id
                )
                  AND (
                    personal.personal_skill_id = row.stored_skill_id
                    OR personal.slug = row.stored_skill_id
                )
                ORDER BY
                    CASE WHEN personal.personal_skill_id = row.stored_skill_id THEN 0 ELSE 1 END,
                    personal.id
                LIMIT 1
            ),
            row.stored_skill_id
        ) AS canonical_skill_id
    FROM agent_skill_rows AS row
    WHERE row.stored_skill_id IS NOT NULL
      AND btrim(row.stored_skill_id) <> ''
), deduplicated_skill_rows AS (
    SELECT
        row.agent_id,
        row.ordinality,
        row.canonical_skill_id,
        row_number() OVER (
            PARTITION BY row.agent_id, row.canonical_skill_id
            ORDER BY row.ordinality
        ) AS duplicate_rank
    FROM canonical_skill_rows AS row
), normalized_skills AS (
    SELECT
        row.agent_id,
        jsonb_agg(to_jsonb(row.canonical_skill_id) ORDER BY row.ordinality) AS skill_ids
    FROM deduplicated_skill_rows AS row
    WHERE row.duplicate_rank = 1
    GROUP BY row.agent_id
)
UPDATE public.hasn_agents AS agent
SET skills = normalized.skill_ids
FROM normalized_skills AS normalized
WHERE agent.id = normalized.agent_id
  AND agent.skills IS DISTINCT FROM normalized.skill_ids;

-- statement-breakpoint

-- 只按引用里已有的精确 version 回查，禁止用 is_latest 猜测历史安装内容。
WITH bundle_rows AS (
    SELECT
        agent.id AS agent_id,
        item.ordinality,
        item.value AS original_ref,
        COALESCE(item.value ->> 'package_id', item.value ->> 'template_id') AS package_id,
        item.value ->> 'version' AS version
    FROM public.hasn_agents AS agent
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(agent.skill_bundles) = 'array' THEN agent.skill_bundles
            ELSE '[]'::jsonb
        END
    ) WITH ORDINALITY AS item(value, ordinality)
), resolved_bundle_rows AS (
    SELECT
        row.agent_id,
        row.ordinality,
        CASE
            WHEN version_row.template_id IS NOT NULL
                 AND NULLIF(version_row.bundle_slug, '') IS NOT NULL
                 AND NULLIF(COALESCE(version_row.content_hash, version_row.file_hash), '') IS NOT NULL
            THEN
                (row.original_ref - 'template_id' - 'needs_refreeze')
                || jsonb_build_object(
                    'package_id', row.package_id,
                    'version', row.version,
                    'content_hash', COALESCE(version_row.content_hash, version_row.file_hash),
                    'bundle_slug', version_row.bundle_slug
                )
            ELSE row.original_ref || jsonb_build_object('needs_refreeze', true)
        END AS frozen_ref
    FROM bundle_rows AS row
    LEFT JOIN hasn_marketplace.marketplace_template_version AS version_row
      ON version_row.template_id = row.package_id
     AND version_row.version = row.version
), normalized_bundles AS (
    SELECT
        row.agent_id,
        jsonb_agg(row.frozen_ref ORDER BY row.ordinality) AS bundle_refs
    FROM resolved_bundle_rows AS row
    GROUP BY row.agent_id
)
UPDATE public.hasn_agents AS agent
SET skill_bundles = normalized.bundle_refs
FROM normalized_bundles AS normalized
WHERE agent.id = normalized.agent_id
  AND agent.skill_bundles IS DISTINCT FROM normalized.bundle_refs;

-- statement-breakpoint

COMMENT ON COLUMN public.hasn_agents.skills IS
    'Agent 直接安装技能引用及个人技能 ID；公共技能与纯技能包成员仅在 Profile 读取时叠加';

-- statement-breakpoint

COMMENT ON COLUMN public.hasn_agents.skill_bundles IS
    '已安装技能包不可变引用 [{package_id, version, content_hash, bundle_slug}]';
