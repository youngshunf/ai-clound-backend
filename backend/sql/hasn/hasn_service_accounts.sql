-- =====================================================
-- 统一通知服务 P2：服务号（Service Account）
-- 设计事实源：docs/hasn-node设计文档/通知系统统一设计/00-统一通知服务设计.md §4.5
-- 一个 source = 一个服务号 = 主人消息列表里一个会话条目（微信服务号效果）。
-- 独立身份，命名空间前缀 sv_，不复用 HasnAgents。
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_service_accounts" (
  "id"            bigserial PRIMARY KEY,
  "sa_hasn_id"    varchar(40)  NOT NULL,
  "kind"          varchar(16)  NOT NULL DEFAULT 'system',
  "ref_id"        varchar(120) NOT NULL DEFAULT '',
  "owner_id"      varchar(40)  NOT NULL DEFAULT '',
  "display_name"  varchar(120) NOT NULL DEFAULT '',
  "avatar"        varchar(500) NOT NULL DEFAULT '',
  "verified"      boolean      NOT NULL DEFAULT false,
  "status"        varchar(16)  NOT NULL DEFAULT 'active',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE UNIQUE INDEX IF NOT EXISTS "uq_service_account_sa_hasn_id"
  ON "public"."hasn_service_accounts" ("sa_hasn_id");
-- 同一主人下，同一 (kind,ref_id) 唯一（一个 source 一个服务号）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_service_account_owner_kind_ref"
  ON "public"."hasn_service_accounts" ("owner_id","kind","ref_id");

COMMENT ON TABLE "public"."hasn_service_accounts" IS 'HASN 服务号（通知来源身份）';
COMMENT ON COLUMN "public"."hasn_service_accounts"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_service_accounts"."sa_hasn_id" IS '服务号 hasn_id（前缀 sv_）';
COMMENT ON COLUMN "public"."hasn_service_accounts"."kind" IS '类型 (app:应用:blue/system:系统:gray/external:外部:orange)';
COMMENT ON COLUMN "public"."hasn_service_accounts"."ref_id" IS '来源引用（app 安装ID / 系统模块名 / 外部源ID）';
COMMENT ON COLUMN "public"."hasn_service_accounts"."owner_id" IS '归属主人 hasn_id';
COMMENT ON COLUMN "public"."hasn_service_accounts"."display_name" IS '展示名';
COMMENT ON COLUMN "public"."hasn_service_accounts"."avatar" IS '头像 URL';
COMMENT ON COLUMN "public"."hasn_service_accounts"."verified" IS '是否官方认证';
COMMENT ON COLUMN "public"."hasn_service_accounts"."status" IS '状态 (active:正常/disabled:停用)';
COMMENT ON COLUMN "public"."hasn_service_accounts"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_service_accounts"."updated_time" IS '更新时间';
