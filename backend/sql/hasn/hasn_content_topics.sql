-- =====================================================
-- HASN 内容 ↔ 话题 关联表
-- 见设计文档 15-社区话题体系设计 §2.2
-- =====================================================
CREATE TABLE "public"."hasn_content_topics" (
  "id"            bigserial PRIMARY KEY,
  "topic_id"      varchar(40) NOT NULL,
  "content_type"  varchar(10) NOT NULL,
  "content_id"    varchar(40) NOT NULL,
  "owner_hasn_id" varchar(40) NOT NULL,
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6),
  UNIQUE("topic_id", "content_type", "content_id")
);

CREATE INDEX idx_content_topics_topic ON "public"."hasn_content_topics"("topic_id", "created_time" DESC);
CREATE INDEX idx_content_topics_content ON "public"."hasn_content_topics"("content_type", "content_id");
CREATE INDEX idx_content_topics_owner ON "public"."hasn_content_topics"("owner_hasn_id");

COMMENT ON TABLE "public"."hasn_content_topics" IS '内容与话题关联表';
COMMENT ON COLUMN "public"."hasn_content_topics"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_content_topics"."topic_id" IS '关联话题 topic_id';
COMMENT ON COLUMN "public"."hasn_content_topics"."content_type" IS '内容类型 (post:帖子/article:文章)';
COMMENT ON COLUMN "public"."hasn_content_topics"."content_id" IS '内容 ID（post_id 或 article_id）';
COMMENT ON COLUMN "public"."hasn_content_topics"."owner_hasn_id" IS '内容责任主体 hasn_id，便于按主体过滤/治理';
COMMENT ON COLUMN "public"."hasn_content_topics"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_content_topics"."updated_time" IS '更新时间';
