-- 设计系统根容器接入平台项目（doc38 层2）：项目只是聚合视角，不改变资源权限。
ALTER TABLE "hasn_designsystem"."design_system"
  ADD COLUMN IF NOT EXISTS "platform_project_id" uuid
  REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS "idx_ds_owner_project"
  ON "hasn_designsystem"."design_system" ("owner_hasn_id", "platform_project_id")
  WHERE "platform_project_id" IS NOT NULL;

COMMENT ON COLUMN "hasn_designsystem"."design_system"."platform_project_id"
  IS '挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目只是视角，不改变权限）';
