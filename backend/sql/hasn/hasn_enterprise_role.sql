-- =====================================================
-- 企业自定义角色 / 部门（应用平台 v3 P3 §4.2(4)）
-- role grantee 的依赖基建：hasn_resource_share.grantee_type='role' 时，自定义角色 /
-- 部门用本表 id 作 grantee_id（内置成员角色用 builtin:owner|admin|member）。
-- =====================================================
CREATE TABLE "public"."hasn_enterprise_role" (
  "id"            bigserial PRIMARY KEY,
  "enterprise_id" bigint NOT NULL,
  "name"          varchar(64) NOT NULL,
  "kind"          varchar(16) NOT NULL DEFAULT 'role',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6),
  UNIQUE ("enterprise_id", "name"),
  CHECK ("kind" IN ('role', 'department'))
);

CREATE INDEX "idx_hasn_enterprise_role_enterprise" ON "public"."hasn_enterprise_role" ("enterprise_id");

COMMENT ON TABLE "public"."hasn_enterprise_role" IS '企业自定义角色 / 部门';
COMMENT ON COLUMN "public"."hasn_enterprise_role"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_enterprise_role"."enterprise_id" IS '所属企业 ID';
COMMENT ON COLUMN "public"."hasn_enterprise_role"."name" IS '角色 / 部门名称（如「销售部」「财务」）';
COMMENT ON COLUMN "public"."hasn_enterprise_role"."kind" IS '类型 (role:角色:blue/department:部门:green)';
COMMENT ON COLUMN "public"."hasn_enterprise_role"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_enterprise_role"."updated_time" IS '更新时间';
