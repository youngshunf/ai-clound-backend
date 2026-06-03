-- =====================================================
-- HASN 文集多级目录树节点表
-- 见设计文档 17-社区文档系统设计 §2.2
-- =====================================================
CREATE TABLE "public"."hasn_doc_nodes" (
  "id"             bigserial PRIMARY KEY,
  "node_id"        varchar(40) NOT NULL UNIQUE,
  "space_id"       varchar(40) NOT NULL,
  "parent_node_id" varchar(40),
  "node_type"      varchar(10) NOT NULL,
  "title"          varchar(200) NOT NULL,
  "article_id"     varchar(40),
  "sort_order"     int NOT NULL DEFAULT 0,
  "depth"          int NOT NULL DEFAULT 0,
  "path"           varchar(500) NOT NULL DEFAULT '',
  "visibility"     varchar(20),
  "password_hash"  varchar(255),
  "pwd_version"    int NOT NULL DEFAULT 0,
  "status"         varchar(20) NOT NULL DEFAULT 'active',
  "created_time"   timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"   timestamptz(6)
);

CREATE INDEX idx_doc_nodes_space ON "public"."hasn_doc_nodes"("space_id", "status");
CREATE INDEX idx_doc_nodes_parent ON "public"."hasn_doc_nodes"("parent_node_id", "sort_order");
CREATE INDEX idx_doc_nodes_article ON "public"."hasn_doc_nodes"("article_id") WHERE "article_id" IS NOT NULL;
CREATE INDEX idx_doc_nodes_subtree ON "public"."hasn_doc_nodes"("space_id", "path");

COMMENT ON TABLE "public"."hasn_doc_nodes" IS '文集多级目录树节点表';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."node_id" IS '全局唯一 ID，格式 dn_{nanoid}';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."space_id" IS '所属文集 space_id';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."parent_node_id" IS '父节点 node_id（NULL=文集根下一级）';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."node_type" IS '节点类型 (directory:目录/article:文章叶子)';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."title" IS '目录名 / 文章在树中的显示标题';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."article_id" IS 'node_type=article 时指向 hasn_articles.article_id';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."sort_order" IS '同级排序';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."depth" IS '物化深度（根下一级=0）';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."path" IS '物化祖先路径，如 /dn_a/dn_b，便于子树前缀查询';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."visibility" IS '可见性 (public:公开/private:私有/password:密码)，NULL=继承最近祖先';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."password_hash" IS 'visibility=password 时的密码哈希';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."pwd_version" IS '密码版本号，改密自增使旧 grant_token 失效';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."status" IS '状态 (active:正常:green/deleted:已删除:red)';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_doc_nodes"."updated_time" IS '更新时间';
