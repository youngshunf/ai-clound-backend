-- =====================================================
-- 圈子内容归属：hasn_posts / hasn_articles 加 circle_id
-- + 重命名旧 visibility 枚举值 circle → workspace_group
-- 见设计文档 16-社区圈子体系设计 §2.3
-- 幂等：IF NOT EXISTS / 条件 UPDATE
-- =====================================================

ALTER TABLE "public"."hasn_posts"    ADD COLUMN IF NOT EXISTS "circle_id" varchar(40);
ALTER TABLE "public"."hasn_articles" ADD COLUMN IF NOT EXISTS "circle_id" varchar(40);

COMMENT ON COLUMN "public"."hasn_posts"."circle_id"    IS '所属圈子 circle_id（NULL=主社区/公共流，非空=只进圈子流）';
COMMENT ON COLUMN "public"."hasn_articles"."circle_id" IS '所属圈子 circle_id（NULL=主社区/公共流，非空=只进圈子流）';

CREATE INDEX IF NOT EXISTS idx_posts_circle    ON "public"."hasn_posts"("circle_id", "status", "published_time" DESC)    WHERE "circle_id" IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_articles_circle ON "public"."hasn_articles"("circle_id", "status", "published_time" DESC) WHERE "circle_id" IS NOT NULL;

-- 命名冲突：释放"圈子"一词，旧 workspace 范围枚举改名
UPDATE "public"."hasn_posts"    SET "visibility" = 'workspace_group' WHERE "visibility" = 'circle';
UPDATE "public"."hasn_articles" SET "visibility" = 'workspace_group' WHERE "visibility" = 'circle';
