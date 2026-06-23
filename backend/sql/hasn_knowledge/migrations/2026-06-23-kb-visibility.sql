-- =====================================================
-- 知识库接入产物级协作（应用平台 v3 §6）：kb 加 visibility 快路径列。
-- kb 已有 scope（== owner_scope 语义：personal/enterprise）+ enterprise_id，本次仅补 visibility。
-- 配合平台表 hasn_resource_share（resource_type='knowledge'）显式授权，给出主体对库的有效权限。
-- 幂等：ADD COLUMN IF NOT EXISTS；存量行默认 private（零行为变化，仍 owner 独占）。
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §4.2/§6
-- =====================================================
ALTER TABLE "hasn_knowledge"."kb" ADD COLUMN IF NOT EXISTS "visibility" varchar(16) NOT NULL DEFAULT 'private';

COMMENT ON COLUMN "hasn_knowledge"."kb"."visibility" IS '可见面 (private:私有:gray/enterprise:企业可见:blue/link:链接:cyan)';

-- 企业可见的快查索引（scope=enterprise 列表过滤）
CREATE INDEX IF NOT EXISTS "idx_kb_enterprise_visible"
  ON "hasn_knowledge"."kb" ("enterprise_id", "visibility")
  WHERE "deleted_time" IS NULL AND "scope" = 'enterprise';
