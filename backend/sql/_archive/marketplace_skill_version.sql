-- 技能市场技能版本表
CREATE TABLE IF NOT EXISTS "public"."marketplace_skill_version" (
  "id" bigserial PRIMARY KEY,
  "skill_id" varchar(100) NOT NULL,
  "version" varchar(50) NOT NULL,
  "changelog" text,
  "git_commit_hash" varchar(64),
  "package_path" varchar(500),
  "file_hash" varchar(64),
  "file_size" int4,
  "is_latest" bool DEFAULT false,
  "published_at" timestamptz(6) DEFAULT CURRENT_TIMESTAMP,
  "created_time" timestamptz(6) DEFAULT CURRENT_TIMESTAMP,
  "updated_time" timestamptz(6),
  UNIQUE("skill_id", "version")
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_skill_version_skill ON marketplace_skill_version(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_version_latest ON marketplace_skill_version(skill_id, is_latest);

-- 字段注释
COMMENT ON TABLE "public"."marketplace_skill_version" IS '技能市场技能版本表';
COMMENT ON COLUMN "public"."marketplace_skill_version"."id" IS '主键ID';
COMMENT ON COLUMN "public"."marketplace_skill_version"."skill_id" IS '关联技能ID';
COMMENT ON COLUMN "public"."marketplace_skill_version"."version" IS '版本号';
COMMENT ON COLUMN "public"."marketplace_skill_version"."changelog" IS '版本更新日志';
COMMENT ON COLUMN "public"."marketplace_skill_version"."git_commit_hash" IS '对应的 Git commit';
COMMENT ON COLUMN "public"."marketplace_skill_version"."package_path" IS '缓存的 zip 包路径';
COMMENT ON COLUMN "public"."marketplace_skill_version"."file_hash" IS 'SHA256 校验值';
COMMENT ON COLUMN "public"."marketplace_skill_version"."file_size" IS '包大小（字节）';
COMMENT ON COLUMN "public"."marketplace_skill_version"."is_latest" IS '是否为最新版本';
COMMENT ON COLUMN "public"."marketplace_skill_version"."published_at" IS '发布时间';
COMMENT ON COLUMN "public"."marketplace_skill_version"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."marketplace_skill_version"."updated_time" IS '更新时间';
