-- =====================================================
-- 演示文稿系统（模块 17）云端权威：page 幻灯片表
-- schema=hasn_deck；deck_id 引用 hasn_deck.deck(id)（同 schema，bigint）；owner_id 冗余便于隔离查询
-- 主键 bigint 自增（对齐 fba id_key）；端云经本地 server_id 映射，无 uid 列。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_deck/page.sql --app hasn_deck --schema hasn_deck --execute
-- 设计事实源：docs/hasn-node设计文档/17-演示文稿系统/01-数据模型.md §4/§9
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_deck";

CREATE TABLE "hasn_deck"."page" (
  "id"             bigserial      PRIMARY KEY,
  "deck_id"        bigint         NOT NULL,
  "owner_id"       varchar(40)    NOT NULL,
  "position"       int            NOT NULL,
  "title"          varchar(255)   NOT NULL DEFAULT '',
  "html"           text           NOT NULL DEFAULT '',
  "notes"          text,
  "layout_intent"  varchar(32),
  "status"         varchar(20)    NOT NULL DEFAULT 'empty',
  "render_state"   jsonb,
  "thumb_asset_id" varchar(64),
  "rev"            bigint         NOT NULL DEFAULT 1,
  "created_time"   timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"   timestamptz(6),
  "deleted_time"   timestamptz(6)
);

CREATE INDEX "idx_page_deck" ON "hasn_deck"."page" ("deck_id", "position");
-- (deck_id, position) 在未删页内唯一（重排事务维护连续性）
CREATE UNIQUE INDEX "uq_page_deck_position" ON "hasn_deck"."page" ("deck_id", "position") WHERE "deleted_time" IS NULL;

COMMENT ON TABLE  "hasn_deck"."page" IS '演示文稿幻灯片（云端权威）';
COMMENT ON COLUMN "hasn_deck"."page"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "hasn_deck"."page"."deck_id" IS '所属 deck（引用 hasn_deck.deck.id，bigint）';
COMMENT ON COLUMN "hasn_deck"."page"."owner_id" IS '归属 owner HASN ID（owner 隔离键，冗余自 deck）';
COMMENT ON COLUMN "hasn_deck"."page"."position" IS '页序（0 起，重排改此值；未删页内 (deck_id, position) 唯一）';
COMMENT ON COLUMN "hasn_deck"."page"."title" IS '页标题（来自 outline，便于侧栏/缩略列表）';
COMMENT ON COLUMN "hasn_deck"."page"."html" IS '单页 HTML（自包含文档或片段，见渲染契约）';
COMMENT ON COLUMN "hasn_deck"."page"."notes" IS '演讲者备注（可空）';
COMMENT ON COLUMN "hasn_deck"."page"."layout_intent" IS '版式意图（冗余自 outline，如 cover/data-focus/comparison）';
COMMENT ON COLUMN "hasn_deck"."page"."status" IS '状态 (empty:空:gray/generating:生成中:orange/generated:已生成:green/edited:已编辑:blue/failed:失败:red)';
COMMENT ON COLUMN "hasn_deck"."page"."render_state" IS '渲染/校验结果缓存（缩略图 asset、canvas 校验、溢出标记，JSON）';
COMMENT ON COLUMN "hasn_deck"."page"."thumb_asset_id" IS '该页缩略图资产 id（预览/列表，可空）';
COMMENT ON COLUMN "hasn_deck"."page"."rev" IS '单调版本（乐观并发 + 同步水位）';
COMMENT ON COLUMN "hasn_deck"."page"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_deck"."page"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_deck"."page"."deleted_time" IS '软删时间（非空=已删）';
