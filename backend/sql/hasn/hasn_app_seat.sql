-- =====================================================
-- 企业应用命名席位（一席一行，指派/回收随成员）
-- 企业购买某 app N 席后，owner/admin 把席位指派给具体成员；未被指派席位的成员
-- 该 app 企业维度 need_seat_assignment。席位挂在 entitlement「套餐」行下（entitlement.seats_total）。
-- 设计事实源：docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §6.1
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_app_seat" (
  "id"             bigserial      PRIMARY KEY,
  "entitlement_id" bigint         NOT NULL,
  "enterprise_id"  bigint         NOT NULL,
  "app_id"         varchar(64)    NOT NULL,
  "member_hasn_id" varchar(40)    NOT NULL,
  "assigned_by"    varchar(40)    NOT NULL,
  "status"         varchar(16)    NOT NULL DEFAULT 'assigned',
  "assigned_at"    timestamptz(6) NOT NULL DEFAULT now(),
  "released_at"    timestamptz(6),
  "created_time"   timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"   timestamptz(6)
);

-- 同一企业同一 app 同一成员至多一个在用席位（只挡「同一成员重复指派」，不挡总量溢出——
-- 总量校验由 assign_seat 在 FOR UPDATE 事务内 count+校验兜，见设计 §6.2 S4）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_app_seat_active"
  ON "public"."hasn_app_seat" ("enterprise_id", "app_id", "member_hasn_id")
  WHERE "status" = 'assigned';
CREATE INDEX IF NOT EXISTS "idx_app_seat_ent"
  ON "public"."hasn_app_seat" ("entitlement_id", "status");

COMMENT ON TABLE "public"."hasn_app_seat" IS '企业应用命名席位（一席一行，指派/回收随成员）';
COMMENT ON COLUMN "public"."hasn_app_seat"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_app_seat"."entitlement_id" IS '所属企业权益「套餐」行 ID（hasn_app_entitlement.id）';
COMMENT ON COLUMN "public"."hasn_app_seat"."enterprise_id" IS '企业 ID';
COMMENT ON COLUMN "public"."hasn_app_seat"."app_id" IS '应用唯一标识';
COMMENT ON COLUMN "public"."hasn_app_seat"."member_hasn_id" IS '被指派席位的成员 owner hasn_id';
COMMENT ON COLUMN "public"."hasn_app_seat"."assigned_by" IS '指派人 owner/admin hasn_id';
COMMENT ON COLUMN "public"."hasn_app_seat"."status" IS '席位状态 (assigned:已指派:green/released:已回收:gray)';
COMMENT ON COLUMN "public"."hasn_app_seat"."assigned_at" IS '指派时间';
COMMENT ON COLUMN "public"."hasn_app_seat"."released_at" IS '回收时间（released 时）';
COMMENT ON COLUMN "public"."hasn_app_seat"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_app_seat"."updated_time" IS '更新时间';
