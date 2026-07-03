-- =====================================================
-- 图坊（imagelab / 图像处理 AI-Native 应用，模块 14 doc30 §5.9 B1）云端轻登记表
-- 云端权威 ID 源：本表 id 即项目「云端权威 ID」（= server_id），供
--   hasn://imagelab/projects/{id} URI 与云端派发/完成卡片深链使用。
-- 图坊 7 张业务表在 daemon 本地 SQLite（本地权威），云端不镜像业务数据；
-- 本表只做「daemon 本地项目(local_ref) → 云端权威 id(server_id)」的轻量映射。
-- 幂等：按 (owner_id, local_ref) upsert——同一 owner 同一 local_ref 重复登记返回同一 id。
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/30-图像处理AI-Native应用(自研引擎·图坊)架构设计.md §5.9 B1
--            + CLAUDE.md「本地 ID 永不上 URI / 云端权威 ID 才是打开依据」铁律（DECKOPEN 范式）
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_imagelab_project" (
  "id"            uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "owner_id"      varchar(40)    NOT NULL,
  "local_ref"     varchar(64)    NOT NULL,
  "name"          varchar(200)   NOT NULL DEFAULT '',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

-- 幂等 upsert 依据：同一 owner 同一 daemon 本地项目只登记一行（重复登记返回同一云端权威 id）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_imagelab_project_owner_local_ref"
  ON "public"."hasn_imagelab_project" ("owner_id", "local_ref");

COMMENT ON TABLE "public"."hasn_imagelab_project" IS '图坊项目云端轻登记（云端权威 ID 源，模块 14 doc30 §5.9 B1）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."id" IS '云端权威 ID（server_id）——凡进 hasn:// URI/卡片/分享路径必用此 ID';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."owner_id" IS '归属主人 hasn_id（行级隔离键，绝不跨 owner）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."local_ref" IS 'daemon 本地项目 ULID（本地权威 ID，仅作映射/去重，本地↔云端映射只存 daemon 侧）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."name" IS '项目名（供派发/完成卡片展示）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."updated_time" IS '更新时间';
