-- =====================================================
-- AI-Native 应用灰度内测访问（云端权威）
-- 谁「被邀请 / 申请并通过」可见可打开某个灰度内测（release_phase=beta_gray）应用
-- 仿 hasn_app_entitlement（付费权益）：同一主体对同一 app 至多一行（再申请=更新同行）
-- 设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（APPBETA 扩展）
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_app_beta_access" (
  "id"           bigserial    PRIMARY KEY,
  "app_id"       varchar(64)  NOT NULL,
  "subject_type" varchar(16)  NOT NULL DEFAULT 'owner',
  "subject_id"   varchar(40)  NOT NULL,
  "source"       varchar(16)  NOT NULL DEFAULT 'apply',
  "status"       varchar(16)  NOT NULL DEFAULT 'pending',
  "note"         varchar(255),
  "decided_by"   varchar(64),
  "decided_at"   timestamptz(6),
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6)
);

-- 同一主体对同一 app 只保留一行（再申请=更新同一行的 status，而非堆新行）
CREATE UNIQUE INDEX IF NOT EXISTS "uq_app_beta_access_subject"
  ON "public"."hasn_app_beta_access" ("app_id", "subject_type", "subject_id");
-- 后台按 app + 状态拉待审/已通过列表
CREATE INDEX IF NOT EXISTS "idx_app_beta_access_app_status"
  ON "public"."hasn_app_beta_access" ("app_id", "status");

COMMENT ON TABLE "public"."hasn_app_beta_access" IS 'AI-Native 应用灰度内测访问（云端权威）';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."app_id" IS '应用唯一标识';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."subject_type" IS '访问主体 (owner:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."subject_id" IS '主体 ID（owner=hasn_id / enterprise=enterprise_id）';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."source" IS '来源 (invite:邀请:blue/apply:申请:orange)';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."status" IS '审批状态 (pending:待审:orange/approved:已通过:green/rejected:已拒绝:red)';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."note" IS '申请理由 / 审批备注';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."decided_by" IS '审批人（admin user id 或 hasn_id；邀请时为操作管理员）';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."decided_at" IS '审批 / 邀请时间';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_app_beta_access"."updated_time" IS '更新时间';
