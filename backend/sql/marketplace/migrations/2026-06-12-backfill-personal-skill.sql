-- 把 marketplace_skill 里 source_type='user' 的个人技能 backfill 进 marketplace_personal_skill。
-- 非破坏性：原 marketplace_skill 行不动（已发布的市场列表保留）；本表是"个人技能库"SSOT 的存量来源。
-- 幂等：personal_skill_id 复用原 skill_id（唯一），ON CONFLICT DO NOTHING；并以 (user_id, slug) NOT EXISTS 兜底。
-- 字段映射：origin=user-upload；body 取 COALESCE(body_zh, body_en)；package_url/file_hash/file_size 取最新版本；
--           已 published 的回指 published_skill_id=skill_id。
INSERT INTO "public"."marketplace_personal_skill" (
  personal_skill_id, user_id, hasn_id, slug, name, description, body, files,
  origin, content_hash, version, package_url, file_hash, file_size,
  icon_url, emoji, category, tags, visibility, published_skill_id,
  synced_at, created_time, updated_time
)
SELECT
  ms.skill_id,
  ms.user_id,
  ms.hasn_id,
  ms.slug,
  ms.name,
  COALESCE(ms.description_zh, ms.description_en),
  COALESCE(ms.body_zh, ms.body_en),
  ms.files,
  'user-upload',
  NULL,
  1,
  v.package_url,
  v.file_hash,
  v.file_size,
  ms.icon_url,
  ms.emoji,
  ms.category,
  COALESCE(ms.tags_zh, ms.tags),
  ms.visibility,
  CASE WHEN ms.status = 'published' THEN ms.skill_id ELSE NULL END,
  ms.synced_at,
  ms.created_time,
  ms.updated_time
FROM "public"."marketplace_skill" ms
LEFT JOIN LATERAL (
  SELECT package_url, file_hash, file_size
  FROM "public"."marketplace_skill_version" sv
  WHERE sv.skill_id = ms.skill_id AND sv.is_latest = true
  LIMIT 1
) v ON true
WHERE ms.source_type = 'user'
  AND ms.user_id IS NOT NULL
  AND ms.slug IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM "public"."marketplace_personal_skill" p
    WHERE p.user_id = ms.user_id AND p.slug = ms.slug
  )
ON CONFLICT (personal_skill_id) DO NOTHING;
