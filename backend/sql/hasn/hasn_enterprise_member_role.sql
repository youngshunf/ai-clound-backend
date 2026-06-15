-- =====================================================
-- 成员 ↔ 企业自定义角色 / 部门（多对多，应用平台 v3 P3 §4.2(4)）
-- 解析 role grantee 时查本表：S 是否担任某自定义角色 / 部门。
-- =====================================================
CREATE TABLE "public"."hasn_enterprise_member_role" (
  "id"            bigserial PRIMARY KEY,
  "enterprise_id" bigint NOT NULL,
  "user_id"       bigint NOT NULL,
  "role_id"       bigint NOT NULL,
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6),
  UNIQUE ("user_id", "role_id")
);

CREATE INDEX "idx_hasn_enterprise_member_role_user" ON "public"."hasn_enterprise_member_role" ("user_id");
CREATE INDEX "idx_hasn_enterprise_member_role_role" ON "public"."hasn_enterprise_member_role" ("role_id");
CREATE INDEX "idx_hasn_enterprise_member_role_enterprise" ON "public"."hasn_enterprise_member_role" ("enterprise_id");

COMMENT ON TABLE "public"."hasn_enterprise_member_role" IS '成员与企业自定义角色 / 部门关联';
COMMENT ON COLUMN "public"."hasn_enterprise_member_role"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_enterprise_member_role"."enterprise_id" IS '所属企业 ID';
COMMENT ON COLUMN "public"."hasn_enterprise_member_role"."user_id" IS '成员 sys_user.id';
COMMENT ON COLUMN "public"."hasn_enterprise_member_role"."role_id" IS '企业角色 / 部门 ID（hasn_enterprise_role.id）';
COMMENT ON COLUMN "public"."hasn_enterprise_member_role"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_enterprise_member_role"."updated_time" IS '更新时间';
