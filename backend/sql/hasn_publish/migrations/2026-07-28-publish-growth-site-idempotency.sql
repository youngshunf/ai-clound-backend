-- Growth 落地页按 Owner + Growth 项目来源保持唯一，配合事务级 advisory lock 防并发重复建站。

CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_growth_site_source
    ON hasn_publish.site (owner_id, source_ref)
    WHERE source_app = 'growth'
      AND source_ref IS NOT NULL
      AND deleted_time IS NULL;
