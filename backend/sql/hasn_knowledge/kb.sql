-- =====================================================
-- 知识库 AI-Native 应用（app_id=knowledge）云端权威：知识库根表
-- 独立 PG schema=hasn_knowledge（ADR-15：AI-Native 应用命名空间与目录 + hasn_ 前缀铁律）
-- 表名去冗余前缀 + schema 限定：hasn_knowledge.kb / folder / document / document_version / agent_kb_grant
-- 共享表（资产 public.hasn_assets、身份 public.hasn_humans/hasn_agents）跨 schema 全限定引用
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_knowledge/kb.sql --app hasn_knowledge --schema hasn_knowledge --execute
-- 设计事实源：docs/hasn-node设计文档/02-记忆与知识库/知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.2
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_knowledge";

CREATE TABLE "hasn_knowledge"."kb" (
  "id"                 bigserial      PRIMARY KEY,
  "owner_id"           varchar(40)    NOT NULL,
  "scope"              varchar(16)    NOT NULL DEFAULT 'personal',
  "enterprise_id"      bigint,
  "name"               varchar(128)   NOT NULL,
  "description"        varchar(512),
  "cover_asset_uri"    varchar(512),
  "ragflow_dataset_id" varchar(64)    NOT NULL,
  "embedding_model"    varchar(128)   NOT NULL,
  "document_count"     int            NOT NULL DEFAULT 0,
  "chunk_count"        int            NOT NULL DEFAULT 0,
  "platform_project_id" uuid          REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL,
  "status"             varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"       timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"       timestamptz(6),
  "deleted_time"       timestamptz(6)
);

CREATE INDEX "idx_kb_owner" ON "hasn_knowledge"."kb" ("owner_id") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_kb_owner_project" ON "hasn_knowledge"."kb" ("owner_id", "platform_project_id") WHERE "platform_project_id" IS NOT NULL;
CREATE UNIQUE INDEX "uq_kb_ragflow_dataset" ON "hasn_knowledge"."kb" ("ragflow_dataset_id") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "hasn_knowledge"."kb" IS '知识库（云端权威；RAGFlow dataset 为派生映射）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_knowledge"."kb"."owner_id" IS '归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."scope" IS '工作区语义 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN "hasn_knowledge"."kb"."enterprise_id" IS '企业 ID（scope=enterprise 必填）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."name" IS '库名';
COMMENT ON COLUMN "hasn_knowledge"."kb"."description" IS '描述';
COMMENT ON COLUMN "hasn_knowledge"."kb"."cover_asset_uri" IS '封面资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."ragflow_dataset_id" IS 'RAGFlow dataset 映射（唯一，派生物）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."embedding_model" IS '向量模型（建库时固化，来自实例 config.default_embd_id）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."document_count" IS '文档数（反规范化计数，状态对账时回写）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."chunk_count" IS '分块数（反规范化计数，状态对账时回写）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."platform_project_id" IS '挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）';
COMMENT ON COLUMN "hasn_knowledge"."kb"."status" IS '状态 (active:正常:green/deleting:删除中:orange)';
COMMENT ON COLUMN "hasn_knowledge"."kb"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_knowledge"."kb"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_knowledge"."kb"."deleted_time" IS '软删除时间';
