-- =====================================================
-- 迁移：创建规划与目标管理应用（app_id=plan）云端权威 9 表
-- schema=hasn_plan：goal / goal_key_result / plan / plan_milestone / todo / event / habit / habit_checkin / preference
-- 事实源：docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §5
-- 生产执行：psql -d huanxing -f backend/sql/hasn_plan/migrations/2026-06-19-create-plan-tables.sql
--
-- 幂等 + 防「空自动建表」陷阱（对齐 designsystem/creator 迁移）：运行中 backend 的 lifespan create_all 可能
--   在迁移前按 model 自动建空表（无完整索引/约束）。每表先「若存在且为空 → DROP」清掉半成品，再用完整
--   DDL 重建（IF NOT EXISTS 兜住「已有数据、不该 DROP」→ no-op）。可重复执行。
--
-- 约定（对齐 hasn_creator/hasn_designsystem 既有 AI-Native 应用表）：
--   主键 id bigserial（不是 UUID）；owner 隔离键 owner_hasn_id varchar(40)（跨模块逻辑引用 hasn_humans，不建硬 FK）；
--   时间 created_time/updated_time timestamptz；同 schema 父子建物理 FK；
--   平台标准列 bound_agent_id/active_work_session_id 为逻辑引用（跨域/避环，不建 FK）；
--   字典字段 COMMENT ON COLUMN ... IS '<含义> (key:label:color/...)'。
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_plan";

-- ── goal — 目标（北极星）───────────────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.goal') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."goal") = 0 THEN
      DROP TABLE "hasn_plan"."goal" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."goal" (
  "id"            bigserial      PRIMARY KEY,
  "owner_hasn_id" varchar(40)    NOT NULL,
  "title"         varchar(255)   NOT NULL,
  "why"           text,
  "category"      varchar(32),
  "target_date"   date,
  "status"        varchar(16)    NOT NULL DEFAULT 'active',
  "progress_pct"  smallint       NOT NULL DEFAULT 0 CHECK ("progress_pct" BETWEEN 0 AND 100),
  "sort"          int            NOT NULL DEFAULT 0,
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);
CREATE INDEX IF NOT EXISTS "idx_plan_goal_owner_status" ON "hasn_plan"."goal" ("owner_hasn_id", "status");
COMMENT ON TABLE  "hasn_plan"."goal" IS '目标（北极星，长期愿景+期限+关键结果）';
COMMENT ON COLUMN "hasn_plan"."goal"."owner_hasn_id" IS '主人 HASN id（owner 隔离键）';
COMMENT ON COLUMN "hasn_plan"."goal"."why" IS '动机';
COMMENT ON COLUMN "hasn_plan"."goal"."category" IS '领域分类 (health:健康:green/career:事业:blue/learning:学习:violet/finance:财务:amber/relationship:关系:pink/life:生活:cyan/other:其它:gray)';
COMMENT ON COLUMN "hasn_plan"."goal"."status" IS '状态 (active:进行中:blue/paused:暂停:orange/done:已达成:green/archived:已归档:gray)';
COMMENT ON COLUMN "hasn_plan"."goal"."progress_pct" IS '进度（派生缓存，由 KR/里程碑/待办完成率计算，不接受前端直填）';

-- ── goal_key_result — 关键结果（KR，n:1 goal）─────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.goal_key_result') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."goal_key_result") = 0 THEN
      DROP TABLE "hasn_plan"."goal_key_result" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."goal_key_result" (
  "id"            bigserial      PRIMARY KEY,
  "goal_id"       bigint         NOT NULL REFERENCES "hasn_plan"."goal"("id") ON DELETE CASCADE,
  "metric"        varchar(255)   NOT NULL,
  "unit"          varchar(32),
  "current_value" numeric(14,2)  NOT NULL DEFAULT 0,
  "target_value"  numeric(14,2)  NOT NULL,
  "direction"     varchar(8)     NOT NULL DEFAULT 'up',
  "sort"          int            NOT NULL DEFAULT 0,
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);
CREATE INDEX IF NOT EXISTS "idx_plan_kr_goal" ON "hasn_plan"."goal_key_result" ("goal_id");
COMMENT ON TABLE  "hasn_plan"."goal_key_result" IS '关键结果（KR，n:1 goal）';
COMMENT ON COLUMN "hasn_plan"."goal_key_result"."metric" IS '指标名';
COMMENT ON COLUMN "hasn_plan"."goal_key_result"."direction" IS '方向 (up:越高越好:green/down:越低越好:blue)';

-- ── plan — 计划/项目（协作型产物）─────────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.plan') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."plan") = 0 THEN
      DROP TABLE "hasn_plan"."plan" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."plan" (
  "id"                     bigserial      PRIMARY KEY,
  "owner_hasn_id"          varchar(40)    NOT NULL,
  "goal_id"                bigint         REFERENCES "hasn_plan"."goal"("id") ON DELETE SET NULL,
  "title"                  varchar(255)   NOT NULL,
  "description"            text,
  "status"                 varchar(16)    NOT NULL DEFAULT 'active',
  "bound_agent_id"         varchar(40),
  "active_work_session_id" varchar(64),
  "sort"                   int            NOT NULL DEFAULT 0,
  "created_time"           timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"           timestamptz(6)
);
CREATE INDEX IF NOT EXISTS "idx_plan_plan_owner_status" ON "hasn_plan"."plan" ("owner_hasn_id", "status");
CREATE INDEX IF NOT EXISTS "idx_plan_plan_goal" ON "hasn_plan"."plan" ("goal_id");
CREATE INDEX IF NOT EXISTS "idx_plan_plan_bound_agent" ON "hasn_plan"."plan" ("bound_agent_id") WHERE "bound_agent_id" IS NOT NULL;
COMMENT ON TABLE  "hasn_plan"."plan" IS '计划/项目（目标的中期拆解，含里程碑/阶段；协作型产物）';
COMMENT ON COLUMN "hasn_plan"."plan"."status" IS '状态 (active:进行中:blue/paused:暂停:orange/done:已完成:green/archived:已归档:gray)';
COMMENT ON COLUMN "hasn_plan"."plan"."bound_agent_id" IS 'AppCollab 平台标准列：协作分身 agent hasn id（逻辑引用，doc21 §4.1）';
COMMENT ON COLUMN "hasn_plan"."plan"."active_work_session_id" IS 'AppCollab 快路径：当前/最近工作会话 id（逻辑引用，权威经 origin_ref 反查，doc21 §4.2）';

-- ── plan_milestone — 里程碑（n:1 plan）────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.plan_milestone') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."plan_milestone") = 0 THEN
      DROP TABLE "hasn_plan"."plan_milestone" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."plan_milestone" (
  "id"           bigserial      PRIMARY KEY,
  "plan_id"      bigint         NOT NULL REFERENCES "hasn_plan"."plan"("id") ON DELETE CASCADE,
  "title"        varchar(255)   NOT NULL,
  "due_date"     date,
  "done"         boolean        NOT NULL DEFAULT false,
  "sort"         int            NOT NULL DEFAULT 0,
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6)
);
CREATE INDEX IF NOT EXISTS "idx_plan_milestone_plan" ON "hasn_plan"."plan_milestone" ("plan_id", "sort");
COMMENT ON TABLE "hasn_plan"."plan_milestone" IS '里程碑（n:1 plan）';

-- ── todo — 待办（最小可执行单元，本应用核心表）────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.todo') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."todo") = 0 THEN
      DROP TABLE "hasn_plan"."todo" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."todo" (
  "id"                     bigserial      PRIMARY KEY,
  "owner_hasn_id"          varchar(40)    NOT NULL,
  "plan_id"                bigint         REFERENCES "hasn_plan"."plan"("id") ON DELETE SET NULL,
  "goal_id"                bigint         REFERENCES "hasn_plan"."goal"("id") ON DELETE SET NULL,
  "title"                  varchar(255)   NOT NULL,
  "notes"                  text,
  "actor"                  varchar(8)     NOT NULL DEFAULT 'owner',
  "autonomy"               varchar(8)     NOT NULL DEFAULT 'auto',
  "status"                 varchar(16)    NOT NULL DEFAULT 'inbox',
  "priority"               smallint       NOT NULL DEFAULT 2 CHECK ("priority" BETWEEN 1 AND 3),
  "estimated_minutes"      int,
  "energy"                 varchar(8),
  "context_tags"           jsonb          NOT NULL DEFAULT '[]'::jsonb,
  "due_at"                 timestamptz(6),
  "deadline_label"         varchar(32),
  "min_block_minutes"      int,
  "active_work_session_id" varchar(64),
  "source"                 varchar(16)    NOT NULL DEFAULT 'manual',
  "completed_time"         timestamptz(6),
  "created_time"           timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"           timestamptz(6)
);
CREATE INDEX IF NOT EXISTS "idx_plan_todo_owner_status" ON "hasn_plan"."todo" ("owner_hasn_id", "status");
CREATE INDEX IF NOT EXISTS "idx_plan_todo_owner_due" ON "hasn_plan"."todo" ("owner_hasn_id", "due_at");
CREATE INDEX IF NOT EXISTS "idx_plan_todo_plan" ON "hasn_plan"."todo" ("plan_id");
CREATE INDEX IF NOT EXISTS "idx_plan_todo_goal" ON "hasn_plan"."todo" ("goal_id");
CREATE INDEX IF NOT EXISTS "idx_plan_todo_aws" ON "hasn_plan"."todo" ("active_work_session_id") WHERE "active_work_session_id" IS NOT NULL;
COMMENT ON TABLE  "hasn_plan"."todo" IS '待办（最小可执行单元，本应用核心表）';
COMMENT ON COLUMN "hasn_plan"."todo"."actor" IS '执行归属 (owner:需你亲为:violet/collab:待你确认:amber/agent:分身自主:cyan)';
COMMENT ON COLUMN "hasn_plan"."todo"."autonomy" IS '分身自主度 (auto:自动:green/review:待审:amber/ask:逐步确认:red)';
COMMENT ON COLUMN "hasn_plan"."todo"."status" IS '状态 (inbox:收件箱:gray/todo:待办:blue/scheduled:已排期:violet/doing:进行中:cyan/waiting_review:待过目:amber/done:已完成:green/cancelled:已取消:gray)';
COMMENT ON COLUMN "hasn_plan"."todo"."priority" IS '优先级 (1:低:gray/2:中:blue/3:高:red)';
COMMENT ON COLUMN "hasn_plan"."todo"."energy" IS '脑力档 (high:高脑力:violet/low:低脑力:gray)';
COMMENT ON COLUMN "hasn_plan"."todo"."source" IS '来源 (chat:对话:cyan/manual:手动:gray/capture:捕获:blue/decompose:分解:violet)';
COMMENT ON COLUMN "hasn_plan"."todo"."active_work_session_id" IS 'AppCollab 快路径：委托执行当前/最近工作会话（逻辑引用，权威经 origin_ref 反查）';

-- ── event — 日程/时间块（日历单元）───────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.event') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."event") = 0 THEN
      DROP TABLE "hasn_plan"."event" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."event" (
  "id"              bigserial      PRIMARY KEY,
  "owner_hasn_id"   varchar(40)    NOT NULL,
  "title"           varchar(255)   NOT NULL,
  "kind"            varchar(8)     NOT NULL DEFAULT 'fixed',
  "actor"           varchar(8),
  "start_at"        timestamptz(6) NOT NULL,
  "end_at"          timestamptz(6) NOT NULL,
  "locked"          boolean        NOT NULL DEFAULT false,
  "all_day"         boolean        NOT NULL DEFAULT false,
  "todo_id"         bigint         REFERENCES "hasn_plan"."todo"("id") ON DELETE CASCADE,
  "recurrence"      jsonb,
  "schedule_reason" text,
  "source"          varchar(16)    NOT NULL DEFAULT 'manual',
  "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"    timestamptz(6),
  CONSTRAINT "chk_plan_event_time" CHECK ("end_at" > "start_at")
);
CREATE INDEX IF NOT EXISTS "idx_plan_event_owner_start" ON "hasn_plan"."event" ("owner_hasn_id", "start_at");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_plan_event_todo" ON "hasn_plan"."event" ("todo_id") WHERE "todo_id" IS NOT NULL;
COMMENT ON TABLE  "hasn_plan"."event" IS '日程/时间块（日历单元；flex 块由 todo_id 权威关联，1:0..1）';
COMMENT ON COLUMN "hasn_plan"."event"."kind" IS '块类型 (fixed:固定:gray/flex:弹性:violet/break:休息:slate)';
COMMENT ON COLUMN "hasn_plan"."event"."actor" IS '执行归属 (owner:亲为:violet/collab:协作:amber/attend:出席:blue)';
COMMENT ON COLUMN "hasn_plan"."event"."locked" IS 'flex 块被拖/锁后置 true，重排不动（§8.1 不变量）';
COMMENT ON COLUMN "hasn_plan"."event"."todo_id" IS 'flex 块实现的待办（权威关联，1:0..1；fixed/break 为 NULL）';
COMMENT ON COLUMN "hasn_plan"."event"."schedule_reason" IS '为什么排在这里的推理串（信任机制 §8.4）';
COMMENT ON COLUMN "hasn_plan"."event"."source" IS '来源 (chat:对话:cyan/manual:手动:gray/capture:捕获:blue/decompose:分解:violet)';

-- ── habit — 习惯/例程（周期性，喂目标进度）───────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.habit') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."habit") = 0 THEN
      DROP TABLE "hasn_plan"."habit" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."habit" (
  "id"               bigserial      PRIMARY KEY,
  "owner_hasn_id"    varchar(40)    NOT NULL,
  "goal_id"          bigint         REFERENCES "hasn_plan"."goal"("id") ON DELETE SET NULL,
  "title"            varchar(255)   NOT NULL,
  "cadence"          varchar(16)    NOT NULL DEFAULT 'daily',
  "target_count"     smallint       NOT NULL DEFAULT 1,
  "energy"           varchar(8),
  "preferred_window" jsonb,
  "streak"           int            NOT NULL DEFAULT 0,
  "best_streak"      int            NOT NULL DEFAULT 0,
  "status"           varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"     timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"     timestamptz(6)
);
CREATE INDEX IF NOT EXISTS "idx_plan_habit_owner_status" ON "hasn_plan"."habit" ("owner_hasn_id", "status");
COMMENT ON TABLE  "hasn_plan"."habit" IS '习惯/例程（周期性待办，打卡积累喂给目标）';
COMMENT ON COLUMN "hasn_plan"."habit"."cadence" IS '节奏 (daily:每日:green/weekly_n:每周N次:blue)';
COMMENT ON COLUMN "hasn_plan"."habit"."energy" IS '脑力档 (high:高脑力:violet/low:低脑力:gray)';
COMMENT ON COLUMN "hasn_plan"."habit"."status" IS '状态 (active:进行中:green/paused:暂停:orange/archived:归档:gray)';

-- ── habit_checkin — 打卡（n:1 habit）─────────────────────────────
DO $$
BEGIN
  IF to_regclass('hasn_plan.habit_checkin') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."habit_checkin") = 0 THEN
      DROP TABLE "hasn_plan"."habit_checkin" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."habit_checkin" (
  "id"           bigserial      PRIMARY KEY,
  "habit_id"     bigint         NOT NULL REFERENCES "hasn_plan"."habit"("id") ON DELETE CASCADE,
  "checkin_date" date           NOT NULL,
  "note"         varchar(255),
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6)
);
CREATE UNIQUE INDEX IF NOT EXISTS "uq_plan_habit_checkin" ON "hasn_plan"."habit_checkin" ("habit_id", "checkin_date");
CREATE INDEX IF NOT EXISTS "idx_plan_habit_checkin_streak" ON "hasn_plan"."habit_checkin" ("habit_id", "checkin_date" DESC);
COMMENT ON TABLE "hasn_plan"."habit_checkin" IS '习惯打卡（一天一卡，UNIQUE(habit_id,checkin_date)）';

-- ── preference — 排程偏好（owner 单例，UNIQUE(owner_hasn_id)）──────
DO $$
BEGIN
  IF to_regclass('hasn_plan.preference') IS NOT NULL THEN
    IF (SELECT count(*) FROM "hasn_plan"."preference") = 0 THEN
      DROP TABLE "hasn_plan"."preference" CASCADE;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "hasn_plan"."preference" (
  "id"                       bigserial      PRIMARY KEY,
  "owner_hasn_id"            varchar(40)    NOT NULL,
  "working_hours"            jsonb          NOT NULL DEFAULT '{}'::jsonb,
  "energy_profile"           jsonb,
  "buffer_minutes"           smallint       NOT NULL DEFAULT 10,
  "no_schedule_windows"      jsonb          NOT NULL DEFAULT '[]'::jsonb,
  "default_autonomy_by_risk" jsonb          NOT NULL DEFAULT '{}'::jsonb,
  "briefing_morning_time"    time,
  "briefing_evening_time"    time,
  "timezone"                 varchar(48)    NOT NULL DEFAULT 'Asia/Shanghai',
  "created_time"             timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"             timestamptz(6)
);
CREATE UNIQUE INDEX IF NOT EXISTS "uq_plan_preference_owner" ON "hasn_plan"."preference" ("owner_hasn_id");
COMMENT ON TABLE  "hasn_plan"."preference" IS '排程偏好（owner 单例，一主人一行；id PK + UNIQUE(owner_hasn_id) 保不变量）';
COMMENT ON COLUMN "hasn_plan"."preference"."working_hours" IS '每日工作时段';
COMMENT ON COLUMN "hasn_plan"."preference"."no_schedule_windows" IS '不可排时段';
