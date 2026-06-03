-- =====================================================
-- HASN 圈子成员与角色表
-- 见设计文档 16-社区圈子体系设计 §2.2
-- =====================================================
CREATE TABLE "public"."hasn_circle_members" (
  "id"                 bigserial PRIMARY KEY,
  "circle_id"          varchar(40) NOT NULL,
  "member_hasn_id"     varchar(40) NOT NULL,
  "member_type"        varchar(10) NOT NULL,
  "owner_hasn_id"      varchar(40) NOT NULL,
  "role"               varchar(20) NOT NULL DEFAULT 'member',
  "status"             varchar(20) NOT NULL DEFAULT 'active',
  "invited_by_hasn_id" varchar(40),
  "joined_time"        timestamptz(6),
  "created_time"       timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"       timestamptz(6),
  UNIQUE("circle_id", "member_hasn_id")
);

CREATE INDEX idx_circle_members_member ON "public"."hasn_circle_members"("member_hasn_id", "status");
CREATE INDEX idx_circle_members_circle ON "public"."hasn_circle_members"("circle_id", "role", "status");
CREATE INDEX idx_circle_members_pending ON "public"."hasn_circle_members"("circle_id", "status")
  WHERE "status" = 'pending';

COMMENT ON TABLE "public"."hasn_circle_members" IS '圈子成员与角色表';
COMMENT ON COLUMN "public"."hasn_circle_members"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_circle_members"."circle_id" IS '所属圈子 circle_id';
COMMENT ON COLUMN "public"."hasn_circle_members"."member_hasn_id" IS '成员 hasn_id（Human 或 Agent）';
COMMENT ON COLUMN "public"."hasn_circle_members"."member_type" IS '成员类型 (human:人类/agent:分身)';
COMMENT ON COLUMN "public"."hasn_circle_members"."owner_hasn_id" IS '成员为 agent 时其主人 hasn_id；human 时=自身';
COMMENT ON COLUMN "public"."hasn_circle_members"."role" IS '角色 (owner:圈主:purple/admin:管理员:blue/member:成员:gray)';
COMMENT ON COLUMN "public"."hasn_circle_members"."status" IS '状态 (active:正常:green/pending:待审批:orange/banned:已封禁:red/left:已退出:gray)';
COMMENT ON COLUMN "public"."hasn_circle_members"."invited_by_hasn_id" IS '邀请人 hasn_id（invite 流程）';
COMMENT ON COLUMN "public"."hasn_circle_members"."joined_time" IS '加入时间';
COMMENT ON COLUMN "public"."hasn_circle_members"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_circle_members"."updated_time" IS '更新时间';
