-- =====================================================
-- 通用网页发布与分享（模块 18）云端权威：revision 不可变版本表
-- 更新 = 新增 revision + 移动 site.current_revision_id；旧 revision 保留可回滚
-- 主键 bigint 自增；site_id 引用 hasn_publish.site(id)；asset_id 引用 public.hasn_assets（跨 schema）
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_publish/revision.sql --app hasn_publish --schema hasn_publish --execute
-- 设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/01-数据模型与权限.md §2.2
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_publish";

CREATE TABLE "hasn_publish"."revision" (
  "id"            bigserial      PRIMARY KEY,
  "site_id"       bigint         NOT NULL,
  "owner_id"      varchar(40)    NOT NULL,
  "seq"           bigint         NOT NULL,
  "asset_id"      varchar(40)    NOT NULL,
  "runtime"       varchar(16)    NOT NULL DEFAULT 'single-html',
  "content_hash"  varchar(64)    NOT NULL DEFAULT '',
  "size_bytes"    bigint         NOT NULL DEFAULT 0,
  "manifest_json" jsonb,
  -- 2026-08-29 发布异步化新增（迁移：migrations/2026-08-29-revision-materialize-status.sql）
  "materialize_status" varchar(16)  NOT NULL DEFAULT 'ready',
  "materialize_error" text,
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6),
  "deleted_time"  timestamptz(6)
);

CREATE UNIQUE INDEX "uq_revision_site_seq" ON "hasn_publish"."revision" ("site_id", "seq");
CREATE INDEX "idx_revision_site" ON "hasn_publish"."revision" ("site_id") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_revision_hash" ON "hasn_publish"."revision" ("site_id", "content_hash");

COMMENT ON TABLE  "hasn_publish"."revision" IS '制品不可变版本（指向私有桶制品对象，URL 不变可回滚）';
COMMENT ON COLUMN "hasn_publish"."revision"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "hasn_publish"."revision"."site_id" IS '所属 site（引用 hasn_publish.site.id，bigint）';
COMMENT ON COLUMN "hasn_publish"."revision"."owner_id" IS '归属 owner HASN ID（owner 隔离键）';
COMMENT ON COLUMN "hasn_publish"."revision"."seq" IS '版本序号（site 内递增；(site_id, seq) 唯一）';
COMMENT ON COLUMN "hasn_publish"."revision"."asset_id" IS '制品在 public.hasn_assets 的 id（access=private；single-html 文件或完整 bundle.zip）';
COMMENT ON COLUMN "hasn_publish"."revision"."runtime" IS '运行时形态 (single-html:单文件:green/bundle-zip:含资产:blue)';
COMMENT ON COLUMN "hasn_publish"."revision"."content_hash" IS '制品内容哈希 sha256（去重/校验/幂等）';
COMMENT ON COLUMN "hasn_publish"."revision"."size_bytes" IS '制品大小（字节）';
COMMENT ON COLUMN "hasn_publish"."revision"."manifest_json" IS 'bundle-zip 发布时解包的子文件清单（name→object_key/mime/size）；single-html 为 null；pending 期间为打包侧原 manifest（无 files）';
COMMENT ON COLUMN "hasn_publish"."revision"."materialize_status" IS '物化状态（ready:已物化/pending:bundle-zip 物化在途(Celery)/failed:物化失败；仅 bundle-zip 会出现非 ready）';
COMMENT ON COLUMN "hasn_publish"."revision"."materialize_error" IS '物化失败的主人可读原因（materialize_status=failed 时非空，可空）';
COMMENT ON COLUMN "hasn_publish"."revision"."created_time" IS '发布时刻（revision 永不改）';
COMMENT ON COLUMN "hasn_publish"."revision"."updated_time" IS '更新时间（revision immutable，恒为空；为对齐 fba DateTimeMixin 保留）';
COMMENT ON COLUMN "hasn_publish"."revision"."deleted_time" IS '软删时间（非空=已删/回收）';
