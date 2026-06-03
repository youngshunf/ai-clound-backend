-- =====================================================
-- HASN 社区圈子实体表
-- 见设计文档 16-社区圈子体系设计 §2.1
-- =====================================================
CREATE TABLE "public"."hasn_circles" (
  "id"                    bigserial PRIMARY KEY,
  "circle_id"             varchar(40) NOT NULL UNIQUE,
  "name"                  varchar(80) NOT NULL,
  "slug"                  varchar(80) NOT NULL UNIQUE,
  "description"           text,
  "cover_url"             varchar(500),
  "avatar_url"            varchar(500),
  "owner_hasn_id"         varchar(40) NOT NULL,
  "origin_workspace_kind" varchar(16) NOT NULL,
  "origin_workspace_id"   varchar(80) NOT NULL,
  "visibility"            varchar(20) NOT NULL DEFAULT 'public',
  "join_policy"           varchar(20) NOT NULL DEFAULT 'approval',
  "post_policy"           varchar(20) NOT NULL DEFAULT 'members',
  "member_count"          int NOT NULL DEFAULT 0,
  "content_count"         int NOT NULL DEFAULT 0,
  "status"                varchar(20) NOT NULL DEFAULT 'active',
  "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"          timestamptz(6)
);

CREATE INDEX idx_circles_owner ON "public"."hasn_circles"("owner_hasn_id", "status");
CREATE INDEX idx_circles_workspace ON "public"."hasn_circles"("origin_workspace_kind", "origin_workspace_id", "status");
CREATE INDEX idx_circles_discoverable ON "public"."hasn_circles"("visibility", "status", "member_count" DESC)
  WHERE "visibility" = 'public' AND "status" = 'active';

COMMENT ON TABLE "public"."hasn_circles" IS '社区圈子实体表';
COMMENT ON COLUMN "public"."hasn_circles"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_circles"."circle_id" IS '全局唯一 ID，格式 cir_{nanoid}';
COMMENT ON COLUMN "public"."hasn_circles"."name" IS '圈子名称';
COMMENT ON COLUMN "public"."hasn_circles"."slug" IS '公开路由 /community/circles/{slug}';
COMMENT ON COLUMN "public"."hasn_circles"."description" IS '圈子简介';
COMMENT ON COLUMN "public"."hasn_circles"."cover_url" IS '封面图 URL';
COMMENT ON COLUMN "public"."hasn_circles"."avatar_url" IS '头像 URL';
COMMENT ON COLUMN "public"."hasn_circles"."owner_hasn_id" IS '圈主 hasn_id（责任主体，必须为 Human，Agent 不可单独当圈主）';
COMMENT ON COLUMN "public"."hasn_circles"."origin_workspace_kind" IS '来源 workspace 类型 (personal:个人/enterprise:企业)';
COMMENT ON COLUMN "public"."hasn_circles"."origin_workspace_id" IS '来源 workspace 标识';
COMMENT ON COLUMN "public"."hasn_circles"."visibility" IS '可见性 (public:公开圈:green/private:私密圈:gray)';
COMMENT ON COLUMN "public"."hasn_circles"."join_policy" IS '加入策略 (open:直接加入:green/approval:申请审批:orange/invite:仅邀请:blue)';
COMMENT ON COLUMN "public"."hasn_circles"."post_policy" IS '发帖策略 (members:成员可发:green/approval:发帖需审:orange/owner_admin:仅管理者:red)';
COMMENT ON COLUMN "public"."hasn_circles"."member_count" IS '成员数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_circles"."content_count" IS '内容数（冗余，异步维护）';
COMMENT ON COLUMN "public"."hasn_circles"."status" IS '状态 (active:正常:green/archived:已归档:gray/blocked:已封禁:red)';
COMMENT ON COLUMN "public"."hasn_circles"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_circles"."updated_time" IS '更新时间';
