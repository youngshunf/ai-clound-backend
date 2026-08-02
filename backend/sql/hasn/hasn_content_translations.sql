-- =====================================================
-- 用户内容译文缓存表（轨道 B · 公共内容按需翻译）
--
-- 设计事实源：docs/hasn-node设计文档/国际化与多语言/00-国际化与多语言总体设计.md §4.2
--
-- 核心约束：
-- 1. 译文是**视图**，不回写原文表（hasn_posts.content 等永远是作者原文）。
-- 2. 缓存键含 source_hash：作者改了原文 → hash 变 → 旧译文自然失效，
--    不会出现「译文对不上原文」的鬼影。旧行保留可查历史，读路径只认当前 hash。
-- 3. engine_version：换模型或改 prompt 时递增，整体失效，不用清表。
-- 4. 不加外键：跨 app 引用，靠 resource_kind + resource_id 逻辑关联。
-- =====================================================
CREATE TABLE "public"."hasn_content_translations" (
  "id"              bigserial PRIMARY KEY,
  "resource_kind"   varchar(32)  NOT NULL,
  "resource_id"     varchar(64)  NOT NULL,
  "field"           varchar(32)  NOT NULL DEFAULT 'content',
  "source_lang"     varchar(16)  NOT NULL,
  "target_lang"     varchar(16)  NOT NULL,
  "source_hash"     varchar(64)  NOT NULL,
  "translated_text" text         NOT NULL,
  "engine"          varchar(64)  NOT NULL,
  "engine_version"  varchar(32)  NOT NULL DEFAULT 'v1',
  "token_usage"     integer      NOT NULL DEFAULT 0,
  "hit_count"       integer      NOT NULL DEFAULT 0,
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6)
);

CREATE UNIQUE INDEX "uk_content_translation"
  ON "public"."hasn_content_translations" ("resource_kind", "resource_id", "field", "target_lang", "source_hash", "engine_version");

CREATE INDEX "idx_content_translation_resource"
  ON "public"."hasn_content_translations" ("resource_kind", "resource_id");

COMMENT ON TABLE  "public"."hasn_content_translations" IS '用户内容译文缓存（译文是视图，不回写原文表）';
COMMENT ON COLUMN "public"."hasn_content_translations"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_content_translations"."resource_kind" IS '资源类型 (post:帖子/article:文章/comment:评论/circle:圈子/profile:名片)';
COMMENT ON COLUMN "public"."hasn_content_translations"."resource_id" IS '资源的云端权威 ID（post_id / article_id / comment_id ...）';
COMMENT ON COLUMN "public"."hasn_content_translations"."field" IS '被翻字段 (content:正文/title:标题/summary:摘要)';
COMMENT ON COLUMN "public"."hasn_content_translations"."source_lang" IS '原文语言（检测所得，如 zh / en）';
COMMENT ON COLUMN "public"."hasn_content_translations"."target_lang" IS '目标语言（如 en / ja / zh-TW）';
COMMENT ON COLUMN "public"."hasn_content_translations"."source_hash" IS '原文 sha256；原文改动即自动失效并重译';
COMMENT ON COLUMN "public"."hasn_content_translations"."translated_text" IS '译文正文';
COMMENT ON COLUMN "public"."hasn_content_translations"."engine" IS '翻译引擎/模型名，如 agnes-2.5-flash';
COMMENT ON COLUMN "public"."hasn_content_translations"."engine_version" IS '翻译管线版本；升级 prompt/模型时递增以整体失效';
COMMENT ON COLUMN "public"."hasn_content_translations"."token_usage" IS '本次翻译消耗 token 数，便于事后成本核算';
COMMENT ON COLUMN "public"."hasn_content_translations"."hit_count" IS '缓存命中次数（共享缓存摊薄效果的观测指标）';
COMMENT ON COLUMN "public"."hasn_content_translations"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_content_translations"."updated_time" IS '更新时间';
