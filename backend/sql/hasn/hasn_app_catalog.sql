-- =====================================================
-- AI-Native 应用目录（云端权威）
-- 一个 app_id 一行：身份 + 显示 + 生命周期 + 商业化定价
-- DB 权威：name/icon/description/上下架/排序/定价数据驱动，可后台 CRUD
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md §4.1
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_app_catalog" (
  "id"                 bigserial    PRIMARY KEY,
  "app_id"             varchar(64)  NOT NULL,
  "name"               varchar(64)  NOT NULL,
  "icon"               varchar(64)  NOT NULL DEFAULT 'app-window',
  "icon_asset_uri"     varchar(255),
  "description"        varchar(255) NOT NULL DEFAULT '',
  "source"             varchar(16)  NOT NULL DEFAULT 'builtin',
  "status"             varchar(16)  NOT NULL DEFAULT 'published',
  "execution_mode"     varchar(24)  NOT NULL DEFAULT 'cloud',
  "scope"              jsonb        NOT NULL DEFAULT '["personal"]'::jsonb,
  "collaboration_mode" varchar(24)  NOT NULL DEFAULT 'none',
  "entry_route"        varchar(128) NOT NULL DEFAULT '',
  "sort_order"         integer      NOT NULL DEFAULT 100,
  "default_mount"      boolean      NOT NULL DEFAULT true,
  "requires_role"      varchar(16),
  "access_type"        varchar(16)  NOT NULL DEFAULT 'free',
  "min_tier"           varchar(16),
  "price_amount"       numeric(12,2),
  "price_unit"         varchar(8)   NOT NULL DEFAULT 'cny',
  "billing_cycle"      varchar(8)   NOT NULL DEFAULT 'once',
  "trial_days"         integer      NOT NULL DEFAULT 0,
  "sku_ref"            varchar(64),
  "manifest_present"   boolean      NOT NULL DEFAULT false,
  "created_time"       timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"       timestamptz(6),
  CONSTRAINT "uq_app_catalog_app_id" UNIQUE ("app_id")
);

CREATE INDEX IF NOT EXISTS "idx_app_catalog_status_sort" ON "public"."hasn_app_catalog" ("status", "sort_order");

COMMENT ON TABLE "public"."hasn_app_catalog" IS 'AI-Native 应用目录（云端权威）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_app_catalog"."app_id" IS '应用唯一标识（与 manifest.app_id / WorkbenchApp.id 一致）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."name" IS '显示名称';
COMMENT ON COLUMN "public"."hasn_app_catalog"."icon" IS '图标 token（前端 ICON_REGISTRY 映射）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."icon_asset_uri" IS '自定义图标资产 URI（优先于 token），hasn://asset/{id} 或 https';
COMMENT ON COLUMN "public"."hasn_app_catalog"."description" IS '应用描述';
COMMENT ON COLUMN "public"."hasn_app_catalog"."source" IS '来源 (builtin:内置:blue/first_party:官方:green/third_party:第三方:gray)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."status" IS '上架状态 (published:已上架:green/disabled:已下架:gray/draft:草稿:orange)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."execution_mode" IS '执行形态 (cloud:云端:blue/embedded_desktop:桌面嵌入:green/local_tool:本地工具:gray)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."scope" IS '可挂载空间类型 JSONB（personal/enterprise）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."collaboration_mode" IS '协作模式 (none:个人:gray/workspace_shared:空间共享:blue)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."entry_route" IS '客户端原生路由';
COMMENT ON COLUMN "public"."hasn_app_catalog"."sort_order" IS '工作台排序（小在前）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."default_mount" IS '新空间是否自动挂载（注册即用）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."requires_role" IS '企业空间所需角色（null=member 即可）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."access_type" IS '准入类型 (free:免费:green/tier:订阅准入:blue/purchase:购买:orange)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."min_tier" IS '订阅准入所需最低档 (pro:专业版/advanced:进阶版/flagship:旗舰版)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."price_amount" IS '购买价格（access_type=purchase 时）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."price_unit" IS '计价单位 (cny:人民币:green/credits:积分:blue)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."billing_cycle" IS '计费周期 (once:一次性:gray/month:包月:blue/year:包年:green)';
COMMENT ON COLUMN "public"."hasn_app_catalog"."trial_days" IS '试用天数（0=无试用）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."sku_ref" IS '对接计费商品/订单 SKU（预留）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."manifest_present" IS '是否有对应 code manifest（部署期回填）';
COMMENT ON COLUMN "public"."hasn_app_catalog"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_app_catalog"."updated_time" IS '更新时间';
