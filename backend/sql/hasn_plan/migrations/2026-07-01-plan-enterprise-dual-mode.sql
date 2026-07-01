-- PLAN-ENT A1 · 规划应用双模化（个人 + 企业日历）数据层
-- 事实源：docs/hasn-node设计文档/19-规划与目标管理/04-规划应用双模化（个人+企业日历）设计.md
--         §3.1/§3.2/§3.3、§4.3、§11 关键边界与决策 PE-D1/PE-6
-- 迁移只 ADD COLUMN + 新表（event_attendee 单独文件），个人数据（enterprise_id IS NULL）零回填零影响
-- （冻结不变量 #1「个人零破坏」）。幂等：ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / COMMENT ON
-- 皆可重复执行；UPDATE 幂等（重复设同值无害）。

SET search_path TO hasn_plan, public;

-- ── 1. 五对象归属列（enterprise_id / dept_id）─────────────────────────────────────
-- 逻辑引用 public.hasn_enterprise / hasn_enterprise_role（部门），**不设硬 FK**（跨 schema + 逻辑引用约定）。
ALTER TABLE goal  ADD COLUMN IF NOT EXISTS enterprise_id bigint;
ALTER TABLE goal  ADD COLUMN IF NOT EXISTS dept_id bigint;
ALTER TABLE plan  ADD COLUMN IF NOT EXISTS enterprise_id bigint;
ALTER TABLE plan  ADD COLUMN IF NOT EXISTS dept_id bigint;
ALTER TABLE todo  ADD COLUMN IF NOT EXISTS enterprise_id bigint;
ALTER TABLE todo  ADD COLUMN IF NOT EXISTS dept_id bigint;
ALTER TABLE event ADD COLUMN IF NOT EXISTS enterprise_id bigint;
ALTER TABLE event ADD COLUMN IF NOT EXISTS dept_id bigint;
ALTER TABLE habit ADD COLUMN IF NOT EXISTS enterprise_id bigint;
ALTER TABLE habit ADD COLUMN IF NOT EXISTS dept_id bigint;

COMMENT ON COLUMN goal.enterprise_id  IS '所属企业 id（NULL=个人目标；逻辑引用 public.hasn_enterprise.id，不设硬 FK）；首期不 surface（PE-D1）';
COMMENT ON COLUMN goal.dept_id        IS '所属部门 id（NULL=不限部门；逻辑引用企业部门）；首期不 surface（PE-D1）';
COMMENT ON COLUMN plan.enterprise_id  IS '所属企业 id（NULL=个人计划；逻辑引用 public.hasn_enterprise.id，不设硬 FK）';
COMMENT ON COLUMN plan.dept_id        IS '所属部门 id（NULL=不限部门）';
COMMENT ON COLUMN todo.enterprise_id  IS '所属企业 id（NULL=个人待办；逻辑引用 public.hasn_enterprise.id，不设硬 FK）';
COMMENT ON COLUMN todo.dept_id        IS '所属部门 id（NULL=不限部门）';
COMMENT ON COLUMN event.enterprise_id IS '所属企业 id（NULL=个人事件；逻辑引用 public.hasn_enterprise.id，不设硬 FK）';
COMMENT ON COLUMN event.dept_id       IS '所属部门 id（NULL=不限部门）';
COMMENT ON COLUMN habit.enterprise_id IS '所属企业 id（NULL=个人习惯）；首期不 surface（PE-D1）';
COMMENT ON COLUMN habit.dept_id       IS '所属部门 id（NULL=不限部门）；首期不 surface（PE-D1）';

-- ── 2. 企业维度查询索引（恒前置 enterprise_id；冻结不变量 #2「企业隔离硬底线」）───────────────
CREATE INDEX IF NOT EXISTS idx_plan_event_ent ON event (enterprise_id, start_at);
CREATE INDEX IF NOT EXISTS idx_plan_todo_ent  ON todo  (enterprise_id, status);
CREATE INDEX IF NOT EXISTS idx_plan_goal_ent  ON goal  (enterprise_id, status);

-- ── 3. event 可见性列（[04] §4.3；两值不含 dept——「同事可见忙闲」由数据范围档 A3 WHO 承载）─────
ALTER TABLE event ADD COLUMN IF NOT EXISTS visibility varchar(16) NOT NULL DEFAULT 'private';
COMMENT ON COLUMN event.visibility IS '企业事件可见性 (private:仅参与者+被授权:gray/public:企业公开:green)（个人事件恒 private，不生效）';

-- ── 4. event.source 字典补充（[04] §3.3；列宽 varchar(16) 够容纳 oa_meeting/oa_interview）─────
COMMENT ON COLUMN event.source IS '来源 (chat:对话:cyan/manual:手动:gray/capture:捕获:blue/decompose:分解:violet/oa_meeting:会议室预定:blue/oa_interview:面试:violet)';

-- ── 5. 应用形态（PE-6 双模应用准入）：plan 目录行 scope + purchasable_by 升双模 ───────────────
-- 现存 catalog 行由 ensure_catalog_seeded（INSERT-only）在首次注册时以 scope=[personal]/purchasable_by=owner 落地，
-- 此处直改活跃行；build_plan_app 的 App.scope 同步升 (personal, enterprise) 保证 validate 闸门 + 新库 seed 一致。
UPDATE public.hasn_app_catalog
   SET scope = '["personal", "enterprise"]'::jsonb,
       purchasable_by = 'both'
 WHERE app_id = 'plan';
