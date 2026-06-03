-- =====================================================
-- HASN 社区文集 / 知识库表
-- 见设计文档 17-社区文档系统设计 §2.1
-- =====================================================
CREATE TABLE "public"."hasn_doc_spaces" (
  "id"                    bigserial PRIMARY KEY,
  "space_id"              varchar(40) NOT NULL UNIQUE,
  "owner_hasn_id"         varchar(40) NOT NULL,
  "author_type"          varchar(10) NOT NULL,
  "author_hasn_id"       varchar(40) NOT NULL,
  "origin_workspace_kind" varchar(16) NOT NULL,
  "origin_workspace_id"   varchar(80) NOT NULL,
  "title"                 varchar(200) NOT NULL,
  "slug"                  varchar(120) NOT NULL,
  "description"           text,
  "cover_url"             varchar(500),
  "default_visibility"    varchar(20) NOT NULL DEFAULT 'private',
  "default_password_hash" varchar(255),
  "node_count"            int NOT NULL DEFAULT 0,
  "article_count"         int NOT NULL DEFAULT 0,
  "status"                varchar(20) NOT NULL DEFAULT 'active',
  "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"          timestamptz(6)
);

CREATE INDEX idx_doc_spaces_owner ON "public"."hasn_doc_spaces"("owner_hasn_id", "status");
CREATE UNIQUE INDEX uq_doc_spaces_owner_slug ON "public"."hasn_doc_spaces"("owner_hasn_id", "slug");

COMMENT ON TABLE "public"."hasn_doc_spaces" IS '社区文集/知识库表';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."space_id" IS '全局唯一 ID，格式 ds_{nanoid}';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."owner_hasn_id" IS '文集责任主体 hasn_id（Agent 文集 = 主人 hasn_id）';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."author_type" IS '创建者身份 (human:人类/agent:分身)';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."author_hasn_id" IS '创建者 hasn_id';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."origin_workspace_kind" IS '来源 workspace 类型 (personal:个人/enterprise:企业)';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."origin_workspace_id" IS '来源 workspace 标识';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."title" IS '文集标题';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."slug" IS '文集在 owner 下唯一，组成公开访问路径';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."description" IS '文集描述';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."cover_url" IS '封面图 URL';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."default_visibility" IS '文集根缺省可见性 (public:公开:green/private:私有:gray/password:密码:orange)';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."default_password_hash" IS 'default_visibility=password 时的密码哈希';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."node_count" IS '节点数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."article_count" IS '文章数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."status" IS '状态 (active:正常:green/archived:已归档:gray/deleted:已删除:red)';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_doc_spaces"."updated_time" IS '更新时间';
