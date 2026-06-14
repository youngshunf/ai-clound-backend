-- =====================================================
-- 演示文稿系统（模块 17）云端权威：revision 版本快照表
-- 撤销/历史/回滚。按节点存（非每次击键）：生成完成、批量编辑、导入、手动保存点
-- 主键 bigint 自增（对齐 fba id_key）；deck_id 引用 hasn_deck.deck(id) bigint；端云经 server_id 映射，无 uid。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_deck/revision.sql --app hasn_deck --schema hasn_deck --execute
-- 设计事实源：docs/hasn-node设计文档/17-演示文稿系统/01-数据模型.md §6/§9
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_deck";

CREATE TABLE "hasn_deck"."revision" (
  "id"           bigserial      PRIMARY KEY,
  "deck_id"      bigint         NOT NULL,
  "owner_id"     varchar(40)    NOT NULL,
  "seq"          int            NOT NULL,
  "label"        varchar(32)    NOT NULL,
  "snapshot"     jsonb          NOT NULL,
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6),
  "deleted_time" timestamptz(6)
);

CREATE UNIQUE INDEX "uq_revision_deck_seq" ON "hasn_deck"."revision" ("deck_id", "seq");

COMMENT ON TABLE  "hasn_deck"."revision" IS '演示文稿版本快照（云端权威历史）';
COMMENT ON COLUMN "hasn_deck"."revision"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "hasn_deck"."revision"."deck_id" IS '所属 deck（引用 hasn_deck.deck.id，bigint）';
COMMENT ON COLUMN "hasn_deck"."revision"."owner_id" IS '归属 owner HASN ID（owner 隔离键）';
COMMENT ON COLUMN "hasn_deck"."revision"."seq" IS '版本序（单调递增；(deck_id, seq) 唯一）';
COMMENT ON COLUMN "hasn_deck"."revision"."label" IS '来源标签 (generated:生成:green/edited:编辑:blue/imported:导入:violet/manual:手动:gray/reverted:回滚:orange)';
COMMENT ON COLUMN "hasn_deck"."revision"."snapshot" IS 'deck + pages 完整快照（JSON；大 deck 可只存基线+diff）';
COMMENT ON COLUMN "hasn_deck"."revision"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_deck"."revision"."updated_time" IS '更新时间（revision immutable，恒为空；为对齐 fba DateTimeMixin 保留）';
COMMENT ON COLUMN "hasn_deck"."revision"."deleted_time" IS '软删时间（非空=已删）';
