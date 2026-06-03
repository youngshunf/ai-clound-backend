-- =====================================================
-- HASN 社区话题实体表
-- 见设计文档 15-社区话题体系设计 §2.1
-- =====================================================
CREATE TABLE "public"."hasn_topics" (
  "id"                   bigserial PRIMARY KEY,
  "topic_id"             varchar(40) NOT NULL UNIQUE,
  "name"                 varchar(80) NOT NULL,
  "slug"                 varchar(80) NOT NULL UNIQUE,
  "description"          text,
  "cover_url"            varchar(500),
  "status"               varchar(20) NOT NULL DEFAULT 'active',
  "merged_into_topic_id" varchar(40),
  "is_featured"          boolean NOT NULL DEFAULT false,
  "is_official"          boolean NOT NULL DEFAULT false,
  "created_by_hasn_id"   varchar(40),
  "content_count"        int NOT NULL DEFAULT 0,
  "follow_count"         int NOT NULL DEFAULT 0,
  "view_count"           bigint NOT NULL DEFAULT 0,
  "last_active_time"     timestamptz(6),
  "created_time"         timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"         timestamptz(6)
);

-- 归一防重：活跃话题按小写名唯一，杜绝同义异写各建一条
CREATE UNIQUE INDEX uq_topics_name_lower ON "public"."hasn_topics"(lower("name")) WHERE "status" = 'active';
CREATE INDEX idx_topics_featured ON "public"."hasn_topics"("is_featured", "last_active_time" DESC) WHERE "status" = 'active';
CREATE INDEX idx_topics_status ON "public"."hasn_topics"("status");

COMMENT ON TABLE "public"."hasn_topics" IS '社区话题实体表';
COMMENT ON COLUMN "public"."hasn_topics"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_topics"."topic_id" IS '全局唯一 ID，格式 tpc_{nanoid}';
COMMENT ON COLUMN "public"."hasn_topics"."name" IS '展示名（可改）';
COMMENT ON COLUMN "public"."hasn_topics"."slug" IS 'URL 友好标识，公开路由 /community/topics/{slug}，改名不改 slug';
COMMENT ON COLUMN "public"."hasn_topics"."description" IS '话题描述';
COMMENT ON COLUMN "public"."hasn_topics"."cover_url" IS '封面图 URL';
COMMENT ON COLUMN "public"."hasn_topics"."status" IS '状态 (active:正常:green/merged:已合并:gray/archived:已归档:orange/blocked:已封禁:red)';
COMMENT ON COLUMN "public"."hasn_topics"."merged_into_topic_id" IS 'status=merged 时指向合并目标 topic_id';
COMMENT ON COLUMN "public"."hasn_topics"."is_featured" IS '运营置顶/推荐';
COMMENT ON COLUMN "public"."hasn_topics"."is_official" IS '官方话题标识';
COMMENT ON COLUMN "public"."hasn_topics"."created_by_hasn_id" IS '创建者 hasn_id（用户自建或运营建，可空=系统归一生成）';
COMMENT ON COLUMN "public"."hasn_topics"."content_count" IS '关联内容数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_topics"."follow_count" IS '关注数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_topics"."view_count" IS '浏览数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_topics"."last_active_time" IS '最近活跃时间';
COMMENT ON COLUMN "public"."hasn_topics"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_topics"."updated_time" IS '更新时间';
