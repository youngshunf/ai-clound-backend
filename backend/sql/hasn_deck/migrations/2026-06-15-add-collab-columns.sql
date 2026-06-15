-- =====================================================
-- 应用平台 v3 §4.2(3)：deck 接入产物级协作的快路径列
-- owner_scope/enterprise_id/visibility = 「默认可见面」快路径，配合平台表 hasn_resource_share 显式授权。
-- page 乐观锁复用既有 rev 列（无需新增 version），update 时校验 expected_version。
-- 幂等：ADD COLUMN IF NOT EXISTS；存量行默认 personal/private（零行为变化）。
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §4.2/§6
-- =====================================================
ALTER TABLE "hasn_deck"."deck" ADD COLUMN IF NOT EXISTS "owner_scope"   varchar(16) NOT NULL DEFAULT 'personal';
ALTER TABLE "hasn_deck"."deck" ADD COLUMN IF NOT EXISTS "enterprise_id" bigint;
ALTER TABLE "hasn_deck"."deck" ADD COLUMN IF NOT EXISTS "visibility"    varchar(16) NOT NULL DEFAULT 'private';

COMMENT ON COLUMN "hasn_deck"."deck"."owner_scope"   IS '归属 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN "hasn_deck"."deck"."enterprise_id" IS '归属企业 ID（owner_scope=enterprise 必填）';
COMMENT ON COLUMN "hasn_deck"."deck"."visibility"    IS '可见面 (private:私有:gray/enterprise:企业可见:blue/link:链接:cyan)';

-- 企业可见的快查索引（owner_scope=enterprise 列表过滤）
CREATE INDEX IF NOT EXISTS "idx_deck_enterprise_visible"
  ON "hasn_deck"."deck" ("enterprise_id", "visibility")
  WHERE "deleted_time" IS NULL AND "owner_scope" = 'enterprise';
