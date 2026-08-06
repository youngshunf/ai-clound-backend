-- =====================================================
-- 知识库联邦挂靠平台项目（doc38 层2 容器级挂靠 · 实施/97 A-C1）：kb 加 platform_project_id 列。
-- 纯组织列：不碰权限、不动 RAGFlow dataset 与索引；NULL = 未挂靠，置 NULL 即摘出且资源本体不动。
-- 外键指 hasn_project.hasn_project(id)，ON DELETE SET NULL（项目 v1 只归档不硬删，此处兜底）。
-- 幂等：ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS；存量行为 NULL（零行为变化）。
-- 设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/04-知识库应用与权限边界.md §7
--             docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §3.2/§5.5
-- =====================================================
ALTER TABLE "hasn_knowledge"."kb"
  ADD COLUMN IF NOT EXISTS "platform_project_id" uuid
  REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL;

COMMENT ON COLUMN "hasn_knowledge"."kb"."platform_project_id" IS '挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）';

-- 并集读反查与「挂靠资源区」按 (owner, project) 过滤，走部分索引（未挂靠行不进索引）。
CREATE INDEX IF NOT EXISTS "idx_kb_owner_project" ON "hasn_knowledge"."kb" ("owner_id", "platform_project_id")
  WHERE "platform_project_id" IS NOT NULL;
