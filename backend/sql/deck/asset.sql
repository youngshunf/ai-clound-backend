-- =====================================================
-- 演示文稿系统（模块 17）云端权威：asset 资产引用表
-- 二进制不入 deck 库——存唤星私有桶 public.hasn_assets，deck 只存引用（同源 D6 回流/附件管线）
-- 主键 bigint 自增（对齐 fba id_key）；deck_id 引用 deck.deck(id) bigint；端云经 server_id 映射，无 uid。
-- 生成：uv run fba codegen generate --sql-file backend/sql/deck/asset.sql --app deck --schema deck --execute
-- 设计事实源：docs/hasn-node设计文档/17-演示文稿系统/01-数据模型.md §5/§9
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "deck";

CREATE TABLE "deck"."asset" (
  "id"           bigserial      PRIMARY KEY,
  "deck_id"      bigint         NOT NULL,
  "owner_id"     varchar(40)    NOT NULL,
  "asset_id"     varchar(64),
  "kind"         varchar(20)    NOT NULL,
  "uri"          varchar(512),
  "mime"         varchar(128),
  "size"         bigint,
  "filename"     varchar(255),
  "local_path"   text,
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6),
  "deleted_time" timestamptz(6)
);

CREATE INDEX "idx_asset_deck" ON "deck"."asset" ("deck_id");

COMMENT ON TABLE  "deck"."asset" IS '演示文稿资产引用（云端权威；二进制存 public.hasn_assets）';
COMMENT ON COLUMN "deck"."asset"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "deck"."asset"."deck_id" IS '所属 deck（引用 deck.deck.id，bigint）';
COMMENT ON COLUMN "deck"."asset"."owner_id" IS '归属 owner HASN ID（owner 隔离键）';
COMMENT ON COLUMN "deck"."asset"."asset_id" IS '云端资产 id（引用 public.hasn_assets.asset_id，hasn://asset/{asset_id}）';
COMMENT ON COLUMN "deck"."asset"."kind" IS '类型 (image:图片:blue/font:字体:gray/export-pptx:PPTX导出:violet/export-pdf:PDF导出:violet/thumb:缩略图:green)';
COMMENT ON COLUMN "deck"."asset"."uri" IS 'hasn://asset/{id}（序列化边界经 resolve_assets 换 CDN 签名 url）';
COMMENT ON COLUMN "deck"."asset"."mime" IS 'MIME 类型';
COMMENT ON COLUMN "deck"."asset"."size" IS '字节大小';
COMMENT ON COLUMN "deck"."asset"."filename" IS '文件名';
COMMENT ON COLUMN "deck"."asset"."local_path" IS '离线兜底本地路径（未上云时；上云后可清，云端通常为空）';
COMMENT ON COLUMN "deck"."asset"."created_time" IS '创建时间';
COMMENT ON COLUMN "deck"."asset"."updated_time" IS '更新时间';
COMMENT ON COLUMN "deck"."asset"."deleted_time" IS '软删时间（非空=已删）';
