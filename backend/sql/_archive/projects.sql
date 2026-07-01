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

 Date: 27/01/2026 13:09:04
*/


-- ----------------------------
-- Table structure for projects
-- ----------------------------
DROP TABLE IF EXISTS "public"."projects";
CREATE TABLE "public"."projects" (
  "uid" varchar(36) COLLATE "pg_catalog"."default" NOT NULL,
  "user_uid" varchar(36) COLLATE "pg_catalog"."default" NOT NULL,
  "industry" varchar(50) COLLATE "pg_catalog"."default",
  "sub_industries" jsonb NOT NULL,
  "brand_name" varchar(100) COLLATE "pg_catalog"."default",
  "brand_tone" varchar(50) COLLATE "pg_catalog"."default",
  "brand_keywords" jsonb NOT NULL,
  "topics" jsonb NOT NULL,
  "keywords" jsonb NOT NULL,
  "account_tags" jsonb NOT NULL,
  "preferred_platforms" jsonb NOT NULL,
  "content_style" varchar(50) COLLATE "pg_catalog"."default",
  "settings" jsonb NOT NULL,
  "is_default" bool NOT NULL,
  "is_deleted" bool NOT NULL,
  "deleted_at" timestamptz(6),
  "server_version" int4 NOT NULL,
  "created_time" timestamptz(6) NOT NULL,
  "updated_time" timestamptz(6)
)
;
ALTER TABLE "public"."projects" OWNER TO "n8n";
COMMENT ON COLUMN "public"."projects"."uid" IS '主键 UID';
COMMENT ON COLUMN "public"."projects"."user_uid" IS '用户 UID';
COMMENT ON COLUMN "public"."projects"."industry" IS '行业';
COMMENT ON COLUMN "public"."projects"."sub_industries" IS '子行业列表';
COMMENT ON COLUMN "public"."projects"."brand_name" IS '品牌名称';
COMMENT ON COLUMN "public"."projects"."brand_tone" IS '品牌调性';
COMMENT ON COLUMN "public"."projects"."brand_keywords" IS '品牌关键词';
COMMENT ON COLUMN "public"."projects"."topics" IS '关注话题';
COMMENT ON COLUMN "public"."projects"."keywords" IS '内容关键词';
COMMENT ON COLUMN "public"."projects"."account_tags" IS '账号标签';
COMMENT ON COLUMN "public"."projects"."preferred_platforms" IS '偏好平台';
COMMENT ON COLUMN "public"."projects"."content_style" IS '内容风格';
COMMENT ON COLUMN "public"."projects"."settings" IS '其他设置';
COMMENT ON COLUMN "public"."projects"."is_default" IS '是否默认项目';
COMMENT ON COLUMN "public"."projects"."is_deleted" IS '是否已删除';
COMMENT ON COLUMN "public"."projects"."deleted_at" IS '删除时间';
COMMENT ON COLUMN "public"."projects"."server_version" IS '服务器版本号';
COMMENT ON COLUMN "public"."projects"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."projects"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."projects" IS '项目表 - 工作区的核心上下文';

-- ----------------------------
-- Indexes structure for table projects
-- ----------------------------
CREATE INDEX "ix_projects_is_deleted" ON "public"."projects" USING btree (
  "is_deleted" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "ix_projects_user_uid" ON "public"."projects" USING btree (
  "user_uid" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table projects
-- ----------------------------
ALTER TABLE "public"."projects" ADD CONSTRAINT "projects_pkey" PRIMARY KEY ("uid");
