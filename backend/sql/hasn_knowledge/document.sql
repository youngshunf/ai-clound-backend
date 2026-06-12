-- =====================================================
-- 知识库文档（文档元数据 + 原生文档正文）
-- kind=file：上传文件，原件权威存储在平台私有桶（asset_uri，D10），RAGFlow 只持解析副本
-- kind=native：原生文档，正文在本表 content（PG 权威，D9）
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_knowledge/document.sql --app hasn_knowledge --schema hasn_knowledge --execute
-- 设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.2 document
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_knowledge";

CREATE TABLE "hasn_knowledge"."document" (
  "id"                  bigserial      PRIMARY KEY,
  "kb_id"               bigint         NOT NULL,
  "folder_id"           bigint,
  "owner_id"            varchar(40)    NOT NULL,
  "kind"                varchar(16)    NOT NULL DEFAULT 'file',
  "name"                varchar(255)   NOT NULL,
  "size_bytes"          bigint         NOT NULL DEFAULT 0,
  "mime_type"           varchar(128),
  "content"             text,
  "asset_uri"           varchar(255),
  "current_version"     int            NOT NULL DEFAULT 0,
  "ragflow_document_id" varchar(64),
  "parse_status"        varchar(16)    NOT NULL DEFAULT 'uploading',
  "parse_error"         varchar(512),
  "chunk_count"         int            NOT NULL DEFAULT 0,
  "source"              varchar(16)    NOT NULL DEFAULT 'ui',
  "agent_hasn_id"       varchar(40),
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6),
  "deleted_time"        timestamptz(6)
);

CREATE INDEX "idx_document_kb_folder" ON "hasn_knowledge"."document" ("kb_id", "folder_id") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_document_owner" ON "hasn_knowledge"."document" ("owner_id") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_document_parse_status" ON "hasn_knowledge"."document" ("kb_id", "parse_status") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "hasn_knowledge"."document" IS '知识库文档（元数据 + 原生正文；file 原件在平台私有桶）';
COMMENT ON COLUMN "hasn_knowledge"."document"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_knowledge"."document"."kb_id" IS '所属知识库 ID（引用 hasn_knowledge.kb）';
COMMENT ON COLUMN "hasn_knowledge"."document"."folder_id" IS '所属目录 ID（NULL=库根，引用 hasn_knowledge.folder）';
COMMENT ON COLUMN "hasn_knowledge"."document"."owner_id" IS '归属 owner HASN ID（冗余，行级隔离热路径免 JOIN）';
COMMENT ON COLUMN "hasn_knowledge"."document"."kind" IS '文档类型 (file:上传文件:blue/native:原生文档:green)';
COMMENT ON COLUMN "hasn_knowledge"."document"."name" IS '文档名';
COMMENT ON COLUMN "hasn_knowledge"."document"."size_bytes" IS '大小字节数（native 取正文派生值）';
COMMENT ON COLUMN "hasn_knowledge"."document"."mime_type" IS 'MIME 类型（native 恒 text/markdown）';
COMMENT ON COLUMN "hasn_knowledge"."document"."content" IS '原生文档 Markdown 正文（仅 native，PG 权威；file 恒 NULL）';
COMMENT ON COLUMN "hasn_knowledge"."document"."asset_uri" IS '原件私有桶引用 hasn://asset/{asset_id}（仅 file，权威存储 D10；native 恒 NULL）';
COMMENT ON COLUMN "hasn_knowledge"."document"."current_version" IS '当前版本号（仅 native，配 document_version）';
COMMENT ON COLUMN "hasn_knowledge"."document"."ragflow_document_id" IS 'RAGFlow document 映射（派生物；同步成功后回填，native 重保存会更换）';
COMMENT ON COLUMN "hasn_knowledge"."document"."parse_status" IS '索引状态 (uploading:上传中:gray/parsing:索引中:blue/parsed:已就绪:green/failed:索引失败:red)';
COMMENT ON COLUMN "hasn_knowledge"."document"."parse_error" IS '索引失败原因（如实落库，零 fake）';
COMMENT ON COLUMN "hasn_knowledge"."document"."chunk_count" IS '分块数';
COMMENT ON COLUMN "hasn_knowledge"."document"."source" IS '创建来源 (ui:用户:blue/agent:分身:purple)';
COMMENT ON COLUMN "hasn_knowledge"."document"."agent_hasn_id" IS '创建分身 HASN ID（source=agent 时归因）';
COMMENT ON COLUMN "hasn_knowledge"."document"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_knowledge"."document"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_knowledge"."document"."deleted_time" IS '软删除时间';
