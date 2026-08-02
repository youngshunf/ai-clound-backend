-- 把技能包版本中的 "*" 成员依赖一次性解析为不可变 version + content_hash。
-- 只有全部成员都能解析且具备真实内容指纹时才更新；不完整包保持原值，由 Runtime 权威接口显式拒绝。
WITH frozen AS (
    SELECT
        tv.id AS template_version_id,
        jsonb_object_agg(
            dependency.key,
            jsonb_build_object(
                'version', skill_version.version,
                'content_hash', COALESCE(skill_version.content_hash, skill_version.file_hash)
            )
            ORDER BY dependency.key
        ) AS member_snapshots,
        count(*) AS resolved_count,
        (
            SELECT count(*)
            FROM jsonb_object_keys(
                COALESCE(tv.skill_dependencies_versioned, '{}'::jsonb)
            )
        ) AS expected_count
    FROM hasn_marketplace.marketplace_template_version AS tv
    JOIN hasn_marketplace.marketplace_template AS template
      ON template.template_id = tv.template_id
     AND template.template_type = 'skill_pack'
    CROSS JOIN LATERAL jsonb_each(
        COALESCE(tv.skill_dependencies_versioned, '{}'::jsonb)
    ) AS dependency
    JOIN LATERAL (
        SELECT
            version.version,
            version.content_hash,
            version.file_hash
        FROM hasn_marketplace.marketplace_skill_version AS version
        WHERE version.skill_id = dependency.key
          AND COALESCE(version.content_hash, version.file_hash, '') <> ''
          AND (
              (
                  COALESCE(
                      NULLIF(
                          btrim(
                              CASE
                                  WHEN jsonb_typeof(dependency.value) = 'object'
                                      THEN dependency.value ->> 'version'
                                  ELSE dependency.value #>> '{}'
                              END
                          ),
                          ''
                      ),
                      '*'
                  ) = '*'
                  AND version.is_latest = true
              )
              OR version.version = btrim(
                  CASE
                      WHEN jsonb_typeof(dependency.value) = 'object'
                          THEN dependency.value ->> 'version'
                      ELSE dependency.value #>> '{}'
                  END
              )
          )
        ORDER BY version.id DESC
        LIMIT 1
    ) AS skill_version ON true
    GROUP BY tv.id, tv.skill_dependencies_versioned
)
UPDATE hasn_marketplace.marketplace_template_version AS target
SET
    skill_dependencies_versioned = frozen.member_snapshots,
    updated_time = now()
FROM frozen
WHERE target.id = frozen.template_version_id
  AND frozen.expected_count > 0
  AND frozen.resolved_count = frozen.expected_count;
