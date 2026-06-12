-- 个人技能同步表（个人技能库 SSOT）
-- 个人技能管理与 marketplace_skill（公开市场列表）分离：本表是 owner 维度的"个人技能库"，
-- 收纳三种来源——runtime-auto（分身运行时自学）、user-upload（本地目录上传）、agent-published（分身主动发布）。
-- 同步流程：先 upsert 进本表 → 再把技能并入 hasn_agents.skills（agent 技能装配）→ provisioning 跨设备物化。
-- 一键发布到市场：promote 进 marketplace_skill，published_skill_id 回指其 skill_id。
-- 与 marketplace_skill 复用同一套 S3 私有桶打包（s3_storage.upload_skill_package）与正文/文件清单提取（skill_content_extractor）。
CREATE TABLE "public"."marketplace_personal_skill" (
  "id" bigserial PRIMARY KEY,
  "personal_skill_id" varchar(255) COLLATE "pg_catalog"."default" NOT NULL,
  "user_id" int8 NOT NULL,
  "hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  "slug" varchar(100) COLLATE "pg_catalog"."default" NOT NULL,
  "name" varchar(200) COLLATE "pg_catalog"."default" NOT NULL,
  "description" text COLLATE "pg_catalog"."default",
  "body" text COLLATE "pg_catalog"."default",
  "files" text COLLATE "pg_catalog"."default",
  "origin" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'user-upload',
  "source_agent_hasn_id" varchar(40) COLLATE "pg_catalog"."default",
  "content_hash" varchar(64) COLLATE "pg_catalog"."default",
  "version" int4 NOT NULL DEFAULT 1,
  "package_url" varchar(500) COLLATE "pg_catalog"."default",
  "file_hash" varchar(64) COLLATE "pg_catalog"."default",
  "file_size" int4,
  "icon_url" varchar(500) COLLATE "pg_catalog"."default",
  "emoji" varchar(20) COLLATE "pg_catalog"."default",
  "category" varchar(50) COLLATE "pg_catalog"."default",
  "tags" text COLLATE "pg_catalog"."default",
  "visibility" varchar(20) COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'private',
  "published_skill_id" varchar(255) COLLATE "pg_catalog"."default",
  "synced_at" timestamptz(6),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  UNIQUE("personal_skill_id")
);

CREATE INDEX "idx_marketplace_personal_skill_user_id" ON "public"."marketplace_personal_skill" ("user_id");
CREATE INDEX "idx_marketplace_personal_skill_content_hash" ON "public"."marketplace_personal_skill" ("content_hash");
CREATE INDEX "idx_marketplace_personal_skill_origin" ON "public"."marketplace_personal_skill" ("origin");
CREATE INDEX "idx_marketplace_personal_skill_published_skill_id" ON "public"."marketplace_personal_skill" ("published_skill_id");
CREATE UNIQUE INDEX "idx_marketplace_personal_skill_user_slug" ON "public"."marketplace_personal_skill" ("user_id", "slug");

COMMENT ON COLUMN "public"."marketplace_personal_skill"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."personal_skill_id" IS '个人技能唯一标识（ULID）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."user_id" IS '所有者用户 ID';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."hasn_id" IS '所有者 HASN ID';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."slug" IS '技能标识符（owner 内唯一）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."name" IS '技能名称';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."description" IS '技能描述';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."body" IS 'SKILL.md frontmatter 之后的 Markdown 正文';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."files" IS '技能目录文件清单，JSON 数组 [{path,size}]（仅名称与大小）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."origin" IS '来源 (runtime-auto:运行时自学:purple/user-upload:本地上传:blue/agent-published:分身发布:green)';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."source_agent_hasn_id" IS '来源分身 HASN ID（runtime-auto/agent-published 时记录）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."content_hash" IS '技能内容哈希（SHA256，去重与同步比对）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."version" IS '版本号（同 slug 内容变更时 +1）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."package_url" IS '完整包下载 URL（S3 私有桶）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."file_hash" IS '包 SHA256 校验值';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."file_size" IS '包大小（字节）';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."icon_url" IS 'SVG 图标 URL';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."emoji" IS 'emoji 图标';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."category" IS '分类';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."tags" IS '标签，JSON 数组字符串';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."visibility" IS '可见性 (private:私有:gray/public:公开:green)';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."published_skill_id" IS '已发布到市场时指向 marketplace_skill.skill_id';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."synced_at" IS '最后同步时间';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."marketplace_personal_skill"."updated_time" IS '更新时间';
COMMENT ON TABLE "public"."marketplace_personal_skill" IS '个人技能同步表（个人技能库 SSOT）';
