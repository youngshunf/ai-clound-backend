-- =====================================================
-- 通用网页发布与分享（模块 18，app_id=publish）云端权威：site 根表
-- 独立 PG schema=hasn_publish（ADR-15：AI-Native 应用命名空间与目录约定）
-- ⚠️ 命名不对称：app_id=publish，schema/表前缀=hasn_publish（避免与动词 publish 混淆）
-- 表名去冗余前缀 + schema 限定：hasn_publish.site / hasn_publish.revision
-- 共享表（资产 public.hasn_assets、身份 public.hasn_humans/hasn_agents）跨 schema 全限定引用
-- 主键约定：bigint 自增（对齐 fba id_key，codegen 生成 model 即用）；slug 为不可枚举公开短码
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_publish/site.sql --app hasn_publish --schema hasn_publish --execute
-- 设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/01-数据模型与权限.md §2.1
-- scope=app(owner)/agent；owner 隔离强制 owner_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_publish";

CREATE TABLE "hasn_publish"."site" (
  "id"                  bigserial      PRIMARY KEY,
  "owner_id"            varchar(40)    NOT NULL,
  "publisher_agent_id"  varchar(40),
  "kind"                varchar(32)    NOT NULL DEFAULT 'page',
  "title"               varchar(200)   NOT NULL DEFAULT '',
  "slug"                varchar(32)    NOT NULL,
  "source_app"          varchar(32),
  "source_ref"          varchar(80),
  "platform_project_id" uuid          REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL,
  "current_revision_id" bigint,
  "status"              varchar(16)    NOT NULL DEFAULT 'active',
  "visibility"          varchar(16)    NOT NULL DEFAULT 'private',
  "password_hash"       text,
  "password_plain"      text,
  "expires_at"          timestamptz(6),
  "allow_present"       boolean        NOT NULL DEFAULT true,
  "allow_download"      boolean        NOT NULL DEFAULT false,
  "allow_indexing"      boolean        NOT NULL DEFAULT false,
  "view_count"          bigint         NOT NULL DEFAULT 0,
  "rev"                 bigint         NOT NULL DEFAULT 1,
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6),
  "deleted_time"        timestamptz(6)
);

CREATE UNIQUE INDEX "uq_site_slug" ON "hasn_publish"."site" ("slug");
CREATE INDEX "idx_site_owner_status" ON "hasn_publish"."site" ("owner_id", "status") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_site_source" ON "hasn_publish"."site" ("owner_id", "source_app", "source_ref") WHERE "deleted_time" IS NULL;
CREATE INDEX "idx_site_owner_project" ON "hasn_publish"."site" ("owner_id", "platform_project_id") WHERE "platform_project_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_publish"."site" IS '已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）';
COMMENT ON COLUMN "hasn_publish"."site"."id" IS '主键 ID（自增 BigInt；端云经本地 server_id 映射）';
COMMENT ON COLUMN "hasn_publish"."site"."owner_id" IS '发布者 owner HASN ID（owner 隔离键，引用 public.hasn_humans）';
COMMENT ON COLUMN "hasn_publish"."site"."publisher_agent_id" IS '若由 agent 代发布，记发起分身 HASN ID（审计，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."kind" IS '制品类型 (deck:演示文稿:violet/report:报告:blue/page:单页:green/dashboard:看板:orange/other:其它:gray)';
COMMENT ON COLUMN "hasn_publish"."site"."title" IS '展示标题';
COMMENT ON COLUMN "hasn_publish"."site"."slug" IS '不可枚举短码（base62 ≥10 位），分享路径 /s/{slug}';
COMMENT ON COLUMN "hasn_publish"."site"."source_app" IS '来源应用（deck 等，便于回到来源编辑，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."source_ref" IS '来源实体 id（如 deck_id，便于更新/反查，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."platform_project_id" IS '挂靠的平台项目云端权威 UUID（可空；项目只提供联邦归集视角）';
COMMENT ON COLUMN "hasn_publish"."site"."current_revision_id" IS '当前对外版本指针（引用 hasn_publish.revision.id，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."status" IS '状态 (active:生效:green/revoked:已撤销:gray)';
COMMENT ON COLUMN "hasn_publish"."site"."visibility" IS '可见性 (private:私有:gray/password:口令:orange/unlisted:不公开:blue/public:公开:green)';
COMMENT ON COLUMN "hasn_publish"."site"."password_hash" IS 'visibility=password 时的 bcrypt hash（访客解锁校验用，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."password_plain" IS 'visibility=password 时的口令明文（仅 owner/agent 通道可回读，用于主人查看与复制带口令链接；访客面绝不返回，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."expires_at" IS '过期即拒访（含 unlisted/public，可空）';
COMMENT ON COLUMN "hasn_publish"."site"."allow_present" IS '是否允许放映/演讲者模式';
COMMENT ON COLUMN "hasn_publish"."site"."allow_download" IS '是否允许下载原始制品';
COMMENT ON COLUMN "hasn_publish"."site"."allow_indexing" IS 'visibility=public 时是否允许公开收录/搜索引擎索引（默认不收录；unlisted 恒 noindex）';
COMMENT ON COLUMN "hasn_publish"."site"."view_count" IS '访问计数（统计，非鉴权）';
COMMENT ON COLUMN "hasn_publish"."site"."rev" IS '元数据乐观锁/同步游标（每次写 +1）';
COMMENT ON COLUMN "hasn_publish"."site"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_publish"."site"."updated_time" IS '更新时间';
COMMENT ON COLUMN "hasn_publish"."site"."deleted_time" IS '软删时间（非空=已删）';
