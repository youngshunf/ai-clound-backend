-- =====================================================
-- 原生文档版本历史（D9）：版本只增不改；向量化只跟 current_version
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_knowledge/document_version.sql --app hasn_knowledge --schema hasn_knowledge --execute
-- 设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.2 document_version
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_knowledge";

CREATE TABLE "hasn_knowledge"."document_version" (
  "id"            bigserial      PRIMARY KEY,
  "document_id"   bigint         NOT NULL,
  "version_no"    int            NOT NULL,
  "title"         varchar(255)   NOT NULL,
  "content"       text           NOT NULL,
  "source"        varchar(16)    NOT NULL DEFAULT 'ui',
  "agent_hasn_id" varchar(40),
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE UNIQUE INDEX "uq_document_version" ON "hasn_knowledge"."document_version" ("document_id", "version_no");

COMMENT ON TABLE  "hasn_knowledge"."document_version" IS '原生文档版本历史（只增不改）';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."document_id" IS '所属文档 ID（引用 hasn_knowledge.document）';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."version_no" IS '版本号（自 1 递增）';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."title" IS '该版本标题快照';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."content" IS '该版本正文快照';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."source" IS '版本来源 (ui:用户:blue/agent:分身:purple)';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."agent_hasn_id" IS '产生该版本的分身 HASN ID（source=agent 时归因）';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_knowledge"."document_version"."updated_time" IS '更新时间（版本只增不改，惯例保留）';
