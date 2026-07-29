-- ClawHub 改为元数据联邦和上游制品直连后的存量数据收敛。
--
-- 1. 修正历史错误下载路径 `/skills/{slug}/versions/{version}/download`；
-- 2. 清除服务器克隆时代遗留的正文和本地仓库路径；
-- 3. 暂时下架无法证明真实作者的 `clawhub/community/*` 记录。
--
-- 带真实作者的记录会在下一轮元数据同步时补齐逐文件 SHA256 清单。
-- community 记录不会删除；同步服务解析出唯一作者后会迁移到稳定身份，
-- 同名多作者等无法消歧的记录保持下架并记录显式错误。

UPDATE hasn_marketplace.marketplace_skill AS skill
SET
    body_en = NULL,
    body_zh = NULL,
    repo_path = NULL,
    updated_time = now()
WHERE skill.source_type = 'clawhub';

UPDATE hasn_marketplace.marketplace_skill_version AS version
SET
    package_url = 'https://clawhub.ai/api/v1/download'
        || '?slug=' || skill.slug
        || '&version=' || replace(version.version, '+', '%2B')
        || '&ownerHandle=' || split_part(skill.namespace, '/', 2),
    file_hash = NULL,
    updated_time = now()
FROM hasn_marketplace.marketplace_skill AS skill
WHERE version.skill_id = skill.skill_id
  AND skill.source_type = 'clawhub'
  AND skill.namespace LIKE 'clawhub/%'
  AND skill.namespace <> 'clawhub/community'
  AND split_part(skill.namespace, '/', 2) <> '';

UPDATE hasn_marketplace.marketplace_skill
SET
    status = 'unpublished',
    visibility = 'private',
    is_private = true,
    is_common = false,
    updated_time = now()
WHERE source_type = 'clawhub'
  AND (
      namespace = 'clawhub/community'
      OR author_name = 'community'
      OR author_name IS NULL
      OR author_name = ''
  );
