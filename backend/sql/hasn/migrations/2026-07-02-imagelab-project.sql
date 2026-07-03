-- =====================================================
-- 迁移：图坊项目云端轻登记表 hasn_imagelab_project（IMG-P3-cloud，模块 14 doc30 §5.9 B1）
-- 云端权威 ID 源：本表 id（UUID）即项目 server_id，供 hasn://imagelab/projects/{id} 深链。
-- 幂等：CREATE TABLE / INDEX IF NOT EXISTS；(owner_id, local_ref) 唯一约束是登记 upsert 依据。
-- 日期：2026-07-02
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_imagelab_project" (
  "id"            uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "owner_id"      varchar(40)    NOT NULL,
  "local_ref"     varchar(64)    NOT NULL,
  "name"          varchar(200)   NOT NULL DEFAULT '',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE UNIQUE INDEX IF NOT EXISTS "uq_imagelab_project_owner_local_ref"
  ON "public"."hasn_imagelab_project" ("owner_id", "local_ref");

COMMENT ON TABLE "public"."hasn_imagelab_project" IS '图坊项目云端轻登记（云端权威 ID 源，模块 14 doc30 §5.9 B1）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."id" IS '云端权威 ID（server_id）——凡进 hasn:// URI/卡片/分享路径必用此 ID';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."owner_id" IS '归属主人 hasn_id（行级隔离键，绝不跨 owner）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."local_ref" IS 'daemon 本地项目 ULID（本地权威 ID，仅作映射/去重，本地↔云端映射只存 daemon 侧）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."name" IS '项目名（供派发/完成卡片展示）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."updated_time" IS '更新时间';
