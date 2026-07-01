-- ============================================================
-- 代码生成测试 - 任务管理表
-- 数据库: PostgreSQL | Schema: public
-- 用途：验证代码生成器的多 scope 生成功能
-- ============================================================

CREATE TABLE "public"."codegen_test_task" (
    "id"              bigserial PRIMARY KEY,
    "user_id"         bigint NOT NULL,
    "title"           varchar(200) NOT NULL,
    "description"     text,
    "status"          smallint DEFAULT 0,
    "priority"        smallint DEFAULT 1,
    "category"        varchar(64),
    "type"            varchar(32) DEFAULT 'normal',
    "progress"        integer DEFAULT 0,
    "due_date"        date,
    "remark"          varchar(512),
    "created_time"    timestamp DEFAULT now(),
    "updated_time"    timestamp
);

COMMENT ON TABLE "public"."codegen_test_task" IS '测试任务表';
COMMENT ON COLUMN "public"."codegen_test_task"."id" IS '主键ID';
COMMENT ON COLUMN "public"."codegen_test_task"."user_id" IS '用户ID';
COMMENT ON COLUMN "public"."codegen_test_task"."title" IS '任务标题';
COMMENT ON COLUMN "public"."codegen_test_task"."description" IS '任务描述';
COMMENT ON COLUMN "public"."codegen_test_task"."status" IS '状态 (0:待办:blue/1:进行中:orange/2:已完成:green/3:已取消:red)';
COMMENT ON COLUMN "public"."codegen_test_task"."priority" IS '优先级 (1:低:blue/2:中:orange/3:高:red)';
COMMENT ON COLUMN "public"."codegen_test_task"."category" IS '分类 (work:工作:blue/study:学习:green/life:生活:purple)';
COMMENT ON COLUMN "public"."codegen_test_task"."type" IS '类型 (normal:普通:blue/urgent:紧急:red/scheduled:计划:green)';
COMMENT ON COLUMN "public"."codegen_test_task"."progress" IS '进度百分比(0-100)';
COMMENT ON COLUMN "public"."codegen_test_task"."due_date" IS '截止日期';
COMMENT ON COLUMN "public"."codegen_test_task"."remark" IS '备注';
COMMENT ON COLUMN "public"."codegen_test_task"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."codegen_test_task"."updated_time" IS '更新时间';

CREATE INDEX "idx_cg_test_task_user" ON "public"."codegen_test_task"("user_id");
CREATE INDEX "idx_cg_test_task_status" ON "public"."codegen_test_task"("status");
CREATE INDEX "idx_cg_test_task_type" ON "public"."codegen_test_task"("type");
