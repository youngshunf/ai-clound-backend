/*
 Navicat Premium Dump SQL

 Source Server         : localhost_5432
 Source Server Type    : PostgreSQL
 Source Server Version : 160009 (160009)
 Source Host           : localhost:5432
 Source Catalog        : creator_flow
 Source Schema         : public

 Target Server Type    : PostgreSQL
 Target Server Version : 160009 (160009)
 File Encoding         : 65001

 Date: 27/01/2026 17:53:04
*/


-- ----------------------------
-- Table structure for project_topics
-- ----------------------------
DROP TABLE IF EXISTS "public"."project_topics";
CREATE TABLE "public"."project_topics" (
  "uid" varchar(36) COLLATE "pg_catalog"."default" NOT NULL,
  "project_uid" varchar(36) COLLATE "pg_catalog"."default" NOT NULL,
  "user_uid" varchar(36) COLLATE "pg_catalog"."default" NOT NULL,
  "title" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "potential_score" float8 NOT NULL,
  "heat_index" float8 NOT NULL,
  "reason" text COLLATE "pg_catalog"."default" NOT NULL,
  "keywords" jsonb NOT NULL,
  "platform_heat" jsonb NOT NULL,
  "heat_sources" jsonb NOT NULL,
  "trend" jsonb NOT NULL,
  "industry_tags" jsonb NOT NULL,
  "target_audience" jsonb NOT NULL,
  "creative_angles" jsonb NOT NULL,
  "content_outline" jsonb NOT NULL,
  "format_suggestions" jsonb NOT NULL,
  "material_clues" jsonb NOT NULL,
  "risk_notes" jsonb NOT NULL,
  "source_info" jsonb NOT NULL,
  "batch_date" date,
  "source_uid" varchar(128) COLLATE "pg_catalog"."default",
  "status" int4 NOT NULL,
  "is_deleted" bool NOT NULL,
  "deleted_at" timestamptz(6),
  "last_sync_at" timestamptz(6),
  "server_version" int4 NOT NULL,
  "created_time" timestamptz(6) NOT NULL,
  "updated_time" timestamptz(6)
)
;
ALTER TABLE "public"."project_topics" OWNER TO "n8n";
COMMENT ON COLUMN "public"."project_topics"."uid" IS '主键 UID';
COMMENT ON COLUMN "public"."project_topics"."project_uid" IS '项目 UID';
COMMENT ON COLUMN "public"."project_topics"."user_uid" IS '用户 UID';
COMMENT ON COLUMN "public"."project_topics"."title" IS '选题标题';
COMMENT ON COLUMN "public"."project_topics"."potential_score" IS '选题潜力分数';
COMMENT ON COLUMN "public"."project_topics"."heat_index" IS '热度指数';
COMMENT ON COLUMN "public"."project_topics"."reason" IS '推荐理由';
COMMENT ON COLUMN "public"."project_topics"."keywords" IS '标签/关键词';
COMMENT ON COLUMN "public"."project_topics"."platform_heat" IS '平台热度分布';
COMMENT ON COLUMN "public"."project_topics"."heat_sources" IS '热度来源';
COMMENT ON COLUMN "public"."project_topics"."trend" IS '趋势走势';
COMMENT ON COLUMN "public"."project_topics"."industry_tags" IS '适配行业';
COMMENT ON COLUMN "public"."project_topics"."target_audience" IS '目标人群';
COMMENT ON COLUMN "public"."project_topics"."creative_angles" IS '创作角度';
COMMENT ON COLUMN "public"."project_topics"."content_outline" IS '内容结构要点';
COMMENT ON COLUMN "public"."project_topics"."format_suggestions" IS '形式建议';
COMMENT ON COLUMN "public"."project_topics"."material_clues" IS '素材线索';
COMMENT ON COLUMN "public"."project_topics"."risk_notes" IS '风险点';
COMMENT ON COLUMN "public"."project_topics"."source_info" IS '来源信息';
COMMENT ON COLUMN "public"."project_topics"."batch_date" IS '生成批次日期';
COMMENT ON COLUMN "public"."project_topics"."source_uid" IS '幂等键';
COMMENT ON COLUMN "public"."project_topics"."status" IS '状态(0:待选 1:已采纳 2:已忽略)';
COMMENT ON COLUMN "public"."project_topics"."is_deleted" IS '是否已删除';
COMMENT ON COLUMN "public"."project_topics"."deleted_at" IS '删除时间';
COMMENT ON COLUMN "public"."project_topics"."last_sync_at" IS '最近同步时间';
COMMENT ON COLUMN "public"."project_topics"."server_version" IS '服务器版本号';
COMMENT ON COLUMN "public"."project_topics"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."project_topics"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."project_topics" IS '项目私有选题表';

-- ----------------------------
-- Indexes structure for table project_topics
-- ----------------------------
CREATE INDEX "ix_project_topics_batch_date" ON "public"."project_topics" USING btree (
  "batch_date" "pg_catalog"."date_ops" ASC NULLS LAST
);
CREATE INDEX "ix_project_topics_is_deleted" ON "public"."project_topics" USING btree (
  "is_deleted" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "ix_project_topics_project_uid" ON "public"."project_topics" USING btree (
  "project_uid" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_project_topics_source_uid" ON "public"."project_topics" USING btree (
  "source_uid" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "ix_project_topics_user_uid" ON "public"."project_topics" USING btree (
  "user_uid" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table project_topics
-- ----------------------------
ALTER TABLE "public"."project_topics" ADD CONSTRAINT "project_topics_pkey" PRIMARY KEY ("uid");

-- ----------------------------
-- Foreign Keys structure for table project_topics
-- ----------------------------
ALTER TABLE "public"."project_topics" ADD CONSTRAINT "project_topics_project_uid_fkey" FOREIGN KEY ("project_uid") REFERENCES "public"."projects" ("uid") ON DELETE NO ACTION ON UPDATE NO ACTION;
