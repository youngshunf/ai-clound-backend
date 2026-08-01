-- =====================================================
-- 历史图坊本地引用兼容登记表
-- 当前图坊直接使用平台项目 UUID；本表 id 只作为旧客户端的兼容 server_id。
-- 图坊 7 张业务表在 daemon 本地 SQLite（本地权威），云端不镜像业务数据；
-- 本表只做「daemon 历史本地引用(local_ref) → 兼容 id(server_id)」的轻量映射。
-- 幂等：按 (owner_id, local_ref) upsert——同一 owner 同一 local_ref 重复登记返回同一 id。
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/30-图坊/01-架构设计.md §5.9
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

-- 幂等 upsert 依据：同一 owner 同一历史本地引用只登记一行（重复登记返回同一兼容 id）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_imagelab_project_owner_local_ref"
  ON "public"."hasn_imagelab_project" ("owner_id", "local_ref");

COMMENT ON TABLE "public"."hasn_imagelab_project" IS '历史图坊本地引用兼容登记（当前流程直接使用平台项目 UUID）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."id" IS '历史兼容 server_id；当前深链使用平台项目 UUID';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."owner_id" IS '归属主人 hasn_id（行级隔离键，绝不跨 owner）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."local_ref" IS 'daemon 历史本地引用（仅作兼容映射与去重）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."name" IS '历史显示名（供旧卡片展示）';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_imagelab_project"."updated_time" IS '更新时间';
