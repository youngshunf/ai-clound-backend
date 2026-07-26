-- Deck 接入平台项目联邦挂靠（doc38 U11c）。
-- 项目只是跨应用归集视角，不改变 Deck 既有 owner/ACL 判权。
ALTER TABLE "hasn_deck"."deck"
  ADD COLUMN IF NOT EXISTS "platform_project_id" uuid
  REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS "idx_deck_owner_project"
  ON "hasn_deck"."deck" ("owner_id", "platform_project_id")
  WHERE "platform_project_id" IS NOT NULL;

COMMENT ON COLUMN "hasn_deck"."deck"."platform_project_id"
  IS '挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目只是视角，不是权限边界）';
