-- Publish 站点接入平台项目联邦挂靠。

ALTER TABLE "hasn_publish"."site"
  ADD COLUMN IF NOT EXISTS "platform_project_id" uuid;

COMMENT ON COLUMN "hasn_publish"."site"."platform_project_id"
  IS '挂靠的平台项目云端权威 UUID（可空；项目只提供联邦归集视角）';

CREATE INDEX IF NOT EXISTS "idx_site_owner_project"
  ON "hasn_publish"."site" ("owner_id", "platform_project_id")
  WHERE "platform_project_id" IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_publish_site_platform_project'
      AND conrelid = 'hasn_publish.site'::regclass
  ) THEN
    ALTER TABLE "hasn_publish"."site"
      ADD CONSTRAINT "fk_publish_site_platform_project"
      FOREIGN KEY ("platform_project_id")
      REFERENCES "hasn_project"."hasn_project"("id")
      ON DELETE SET NULL;
  END IF;
END
$$;
