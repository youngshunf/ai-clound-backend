-- =====================================================
-- 平台项目（模块 14 doc38，app_id=project）云端权威：hasn_project 根表
-- 独立 PG schema=hasn_project（ADR：AI-Native 应用命名空间与目录）
-- 云端权威 ID 源：本表 id（UUID）即项目「云端权威 ID」（= server_id），凡进 hasn://project/{id}
--   URI / 卡片 / 分享路径 / 深链一律用此 ID（守「本地 ID 永不上 URI」铁律）。
-- 定位（doc38）：第三条轴（项目轴）——只回答「为了哪件事」；不是权限边界、不是应用挂载点、
--   不接管应用容器。各应用容器 / 产物 / 工作会话以可空 project_id 联邦挂靠到本表。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_project/hasn_project.sql --app hasn_project --schema hasn_project --execute
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §3/§4
-- scope=app(owner)/agent；owner 隔离强制 owner_id = <jwt owner>
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_project";

CREATE TABLE "hasn_project"."hasn_project" (
  "id"              uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
  "owner_id"        varchar(40)    NOT NULL,
  "name"            varchar(200)   NOT NULL,
  "goal"            text,
  "cover_asset_uri" text,
  "status"          varchar(16)    NOT NULL DEFAULT 'active',
  "bound_agent_id"  varchar(40),
  "client_request_id" varchar(128),
  "enterprise_id"   uuid,
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6)
);

CREATE INDEX "idx_hasn_project_owner_status" ON "hasn_project"."hasn_project" ("owner_id", "status");
CREATE INDEX "idx_hasn_project_enterprise" ON "hasn_project"."hasn_project" ("enterprise_id") WHERE "enterprise_id" IS NOT NULL;
CREATE UNIQUE INDEX "uq_hasn_project_owner_client_request"
    ON "hasn_project"."hasn_project" ("owner_id", "client_request_id");

COMMENT ON TABLE  "hasn_project"."hasn_project" IS '平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."id" IS '云端权威 ID（server_id）——凡进 hasn://project/{id} URI/卡片/分享路径必用此 ID';
COMMENT ON COLUMN "hasn_project"."hasn_project"."owner_id" IS '归属主人 HASN ID（owner 隔离键，逻辑引用 public.hasn_humans，绝不跨 owner）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."name" IS '项目名';
COMMENT ON COLUMN "hasn_project"."hasn_project"."goal" IS '一句话目标（分身建项目时采集，供聚合视图与派发上下文注入，可空）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."cover_asset_uri" IS '封面图资产引用（hasn://asset/{id}，来源=上传/素材下载/AI 生成；序列化边界换 CDN 签名 URL，不存直链；可空回落品牌渐变+首字）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."status" IS '状态 (active:进行中:blue/archived:已归档:gray)';
COMMENT ON COLUMN "hasn_project"."hasn_project"."bound_agent_id" IS '默认协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；对齐 doc21 AppCollab，列名铁律 doc38 §8）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."client_request_id" IS '创建请求幂等键（主人范围唯一；如两阶段派发 launch_trace_id；可空表示普通非幂等创建）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."enterprise_id" IS '企业归属（双模化，个人 NULL / 企业非空，对齐 GE，可空）';
COMMENT ON COLUMN "hasn_project"."hasn_project"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_project"."hasn_project"."updated_time" IS '更新时间';
