-- =====================================================
-- 知识库目录树（D9）：本应用 PG 纯组织元数据，RAGFlow 不感知（其自身无目录概念）
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_knowledge/folder.sql --app hasn_knowledge --schema hasn_knowledge --execute
-- 设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.2 folder
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_knowledge";

CREATE TABLE "hasn_knowledge"."folder" (
  "id"           bigserial      PRIMARY KEY,
  "kb_id"        bigint         NOT NULL,
  "owner_id"     varchar(40)    NOT NULL,
  "parent_id"    bigint,
  "name"         varchar(128)   NOT NULL,
  "sort_order"   int            NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6),
  "deleted_time" timestamptz(6)
);

CREATE INDEX "idx_folder_kb" ON "hasn_knowledge"."folder" ("kb_id") WHERE "deleted_time" IS NULL;
-- 同层同名拒绝（软删除外；parent_id NULL=库根，用 COALESCE 归一避免 NULL 不参与唯一约束）
CREATE UNIQUE INDEX "uq_folder_sibling_name" ON "hasn_knowledge"."folder" ("kb_id", COALESCE("parent_id", 0), "name") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "hasn_knowledge"."folder" IS '知识库目录树（纯组织元数据，RAGFlow 不感知）';
COMMENT ON COLUMN "hasn_knowledge"."folder"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_knowledge"."folder"."kb_id" IS '所属知识库 ID（引用 hasn_knowledge.kb）';
COMMENT ON COLUMN "hasn_knowledge"."folder"."owner_id" IS '归属 owner HASN ID（owner 隔离键）';
COMMENT ON COLUMN "hasn_knowledge"."folder"."parent_id" IS '父目录 ID（NULL=库根；任意层嵌套，自引用）';
COMMENT ON COLUMN "hasn_knowledge"."folder"."name" IS '目录名（同层同名拒绝）';
COMMENT ON COLUMN "hasn_knowledge"."folder"."sort_order" IS '排序序号';
COMMENT ON COLUMN "hasn_knowledge"."folder"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_knowledge"."folder"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_knowledge"."folder"."deleted_time" IS '软删除时间（删除仅限空目录）';
