-- =====================================================
-- 通用产物共享表（平台级，public schema）
-- 一张表管所有 AI-Native 文档型应用（deck/doc/个人知识库…）的显式协作授权。
-- visibility（产物自身列）是「默认可见面」快路径；本表是「显式精细授权」慢路径，二者并存。
-- 协作者 grantee 五类，覆盖唤星「人 + 分身」社交网络（共享给分身=其工具可代主人操作该产物）。
-- 业务系统型应用（获客/CRM）不走本表（走企业租户 + 角色裁剪），见 §6.7。
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §4.2(2)/§6.3
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_resource_share" (
  "id"             bigserial    PRIMARY KEY,
  "resource_type"  varchar(32)  NOT NULL,
  "resource_id"    varchar(64)  NOT NULL,
  "owner_hasn_id"  varchar(64)  NOT NULL,
  "grantee_type"   varchar(16)  NOT NULL,
  "grantee_id"     varchar(64),
  "permission"     varchar(16)  NOT NULL,
  "granted_by"     varchar(64)  NOT NULL,
  "status"         varchar(16)  NOT NULL DEFAULT 'active',
  "created_time"   timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"   timestamptz(6)
);

-- 同一产物对同一 grantee 只保留一条 active 授权（重复授权=改权限而非新行）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_resource_share_grantee"
  ON "public"."hasn_resource_share" ("resource_type", "resource_id", "grantee_type", "grantee_id")
  WHERE "status" = 'active';
CREATE INDEX IF NOT EXISTS "idx_resource_share_resource"
  ON "public"."hasn_resource_share" ("resource_type", "resource_id");
CREATE INDEX IF NOT EXISTS "idx_resource_share_grantee"
  ON "public"."hasn_resource_share" ("grantee_type", "grantee_id");
CREATE INDEX IF NOT EXISTS "idx_resource_share_owner"
  ON "public"."hasn_resource_share" ("owner_hasn_id");

COMMENT ON TABLE "public"."hasn_resource_share" IS '通用产物共享表（平台级显式协作授权）';
COMMENT ON COLUMN "public"."hasn_resource_share"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_resource_share"."resource_type" IS '产物类型 (deck:演示文稿:blue/doc:文档:cyan/knowledge:知识库:purple)';
COMMENT ON COLUMN "public"."hasn_resource_share"."resource_id" IS '产物主键（deck 为 bigint 文本）';
COMMENT ON COLUMN "public"."hasn_resource_share"."owner_hasn_id" IS '产物归属者 hasn_id（冗余，便于「我共享出去的」反查）';
COMMENT ON COLUMN "public"."hasn_resource_share"."grantee_type" IS '被授权对象类型 (human:人:blue/agent:分身:purple/enterprise:企业:cyan/role:角色:orange/link:链接:gray)';
COMMENT ON COLUMN "public"."hasn_resource_share"."grantee_id" IS '被授权对象 ID（human/agent=hasn_id；enterprise=enterprise_id；role=builtin:xxx 或 role.id；link=null）';
COMMENT ON COLUMN "public"."hasn_resource_share"."permission" IS '权限档 (viewer:查看:gray/editor:编辑:blue/manager:管理:green)';
COMMENT ON COLUMN "public"."hasn_resource_share"."granted_by" IS '授权操作者 hasn_id';
COMMENT ON COLUMN "public"."hasn_resource_share"."status" IS '授权状态 (active:生效:green/revoked:已撤销:red)';
COMMENT ON COLUMN "public"."hasn_resource_share"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_resource_share"."updated_time" IS '更新时间';
