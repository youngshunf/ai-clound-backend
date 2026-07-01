"""规划与目标管理（app_id=plan）统一业务 service（owner 隔离的权威实现）。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §5。

与 designsystem 同范式：codegen 生成的泛型 per-table service/api 留盘不接线（引用 user_id，
与本应用 owner_hasn_id/HASN 身份模型不兼容）；真实业务面由本 service + 自定义 app/agent API 承载。

owner 隔离铁律（设计 §5.5#2）：所有读写按 `owner_hasn_id` 过滤，**owner 身份永远由调用方
（Owner JWT / Agent JWT claims）解析后传入，绝不接受请求体携带的身份**。子对象（KR/里程碑/打卡）
经父对象归属校验，间接落到 owner。`progress_pct` 派生缓存不接受客户端直填（§5.5#4）。
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_plan.model import (
    Event,
    Goal,
    GoalKeyResult,
    Habit,
    HabitCheckin,
    Plan,
    PlanMilestone,
    Preference,
    Todo,
)
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ── 字段白名单（owner_hasn_id/id/时间戳/派生列永不在内）────────────────────────────
_GOAL_FIELDS = {'title', 'why', 'category', 'target_date', 'status', 'sort'}  # progress_pct 派生，不可直填
_KR_FIELDS = {'metric', 'unit', 'current_value', 'target_value', 'direction', 'sort'}
_PLAN_FIELDS = {'goal_id', 'title', 'description', 'status', 'bound_agent_id', 'active_work_session_id', 'sort'}
_MILESTONE_FIELDS = {'title', 'due_date', 'done', 'sort'}
_TODO_FIELDS = {
    'plan_id',
    'goal_id',
    'title',
    'notes',
    'actor',
    'autonomy',
    'status',
    'priority',
    'estimated_minutes',
    'energy',
    'context_tags',
    'due_at',
    'deadline_label',
    'min_block_minutes',
    'active_work_session_id',
    'output_spec',
    'source',
    'completed_time',
}
_EVENT_FIELDS = {
    'title',
    'kind',
    'actor',
    'start_at',
    'end_at',
    'locked',
    'all_day',
    'todo_id',
    'recurrence',
    'schedule_reason',
    'source',
    'visibility',
}
_HABIT_FIELDS = {
    'goal_id',
    'title',
    'cadence',
    'target_count',
    'energy',
    'preferred_window',
    'streak',
    'best_streak',
    'status',
}
_CHECKIN_FIELDS = {'checkin_date', 'note'}
_PREFERENCE_FIELDS = {
    'working_hours',
    'energy_profile',
    'buffer_minutes',
    'no_schedule_windows',
    'default_autonomy_by_risk',
    'briefing_morning_time',
    'briefing_evening_time',
    'timezone',
}


# 时间类字段需把 ISO 字符串强制转成 date/datetime/time（Agent/Owner API 的 body 是 untyped dict，
# 时间值经 JSON 必为字符串；PostgreSQL 不做 date=varchar 隐式转换，不转则查询/写入皆报错）。
_DATE_KEYS = {'target_date', 'due_date', 'checkin_date'}
_DATETIME_KEYS = {'due_at', 'completed_time', 'start_at', 'end_at'}
_TIME_KEYS = {'briefing_morning_time', 'briefing_evening_time'}


def _coerce_temporal(key: str, value: Any) -> Any:
    """ISO 字符串 → date/datetime/time（已是对应类型或非时间字段则原样返回；非法格式如实抛 ValueError）。"""
    if not isinstance(value, str):
        return value
    if key in _DATE_KEYS:
        return date.fromisoformat(value)
    if key in _DATETIME_KEYS:
        return datetime.fromisoformat(value)
    if key in _TIME_KEYS:
        return time.fromisoformat(value)
    return value


# priority 列是 SMALLINT(1:低/2:中/3:高)，但 MCP 工具 schema 历史声明为 string，分身常传 "high"/"medium"/"low"
# 语义值（也可能传数字串 "3" 或已是 int）。untyped body 不转则字符串直写 SMALLINT 列 → PostgreSQL 报错 500。
# 此处统一归一化：语义词/数字串/int 一律落成 1–3 的整数，无法识别回落默认中等，绝不把字符串透传到 DB。
_PRIORITY_LABELS = {'low': 1, 'medium': 2, 'normal': 2, 'mid': 2, 'high': 3, 'urgent': 3}
_PRIORITY_DEFAULT = 2


def _coerce_priority(value: Any) -> int:
    """priority 语义值/数字串/int → SMALLINT(1–3)；无法识别回落默认中等。"""
    if isinstance(value, bool):  # bool 是 int 子类，单独挡（True/False 非有效优先级）
        return _PRIORITY_DEFAULT
    if isinstance(value, int):
        return min(max(value, 1), 3)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _PRIORITY_LABELS:
            return _PRIORITY_LABELS[s]
        if s.lstrip('-').isdigit():
            return min(max(int(s), 1), 3)
    return _PRIORITY_DEFAULT


def _coerce_field(key: str, value: Any) -> Any:
    """字段值归一化：priority 语义/数字串 → SMALLINT；时间字段 ISO 串 → date/datetime/time。"""
    if key == 'priority':
        return _coerce_priority(value)
    return _coerce_temporal(key, value)


def _pick(fields: set[str], data: dict[str, Any]) -> dict[str, Any]:
    """仅保留白名单字段且值不为 None（None 视为「不设置」，由 DB 默认/原值兜底）；
    时间类字段 ISO 串转类型、priority 归一化为 SMALLINT。"""
    return {k: _coerce_field(k, v) for k, v in data.items() if k in fields and v is not None}


def _ownership(enterprise_id: int | None, dept_id: int | None) -> dict[str, Any]:
    """企业归属注入（PLAN-ENT，[04] §6.1）。

    enterprise_id/dept_id 由**服务端**（工具层 PE-7 空间解析 / 到期派发从条目行读）解析后传入，
    **绝不来自 client `data`**（不在白名单，冻结不变量 #5「owner+enterprise 双维」）。个人 = 两者 None
    → 返回空 dict → 走 [01] 个人路径（`enterprise_id IS NULL`，不变量 #1「个人零破坏」）。
    dept_id 仅在 enterprise_id 存在时有意义（部门属于企业）。
    """
    out: dict[str, Any] = {}
    if enterprise_id is not None:
        out['enterprise_id'] = enterprise_id
        if dept_id is not None:
            out['dept_id'] = dept_id
    return out


def serialize(row: Any) -> dict[str, Any]:
    """SQLAlchemy 行 → JSON 安全 dict（datetime→ISO、date→ISO、Decimal→float）。"""
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        v = getattr(row, col.name)
        if isinstance(v, (datetime, date)):
            out[col.name] = v.isoformat()
        elif isinstance(v, Decimal):
            out[col.name] = float(v)
        else:
            out[col.name] = v
    return out


class PlanService:
    """plan 应用 owner 隔离 CRUD + 「今日」聚合。所有方法的 owner 由调用方解析后传入。"""

    # ── goal ──────────────────────────────────────────────────────────────────
    async def list_goals(self, db: AsyncSession, *, owner: str, status: str | None = None) -> list[dict]:
        stmt = sa.select(Goal).where(Goal.owner_hasn_id == owner)
        if status:
            stmt = stmt.where(Goal.status == status)
        stmt = stmt.order_by(Goal.sort.asc(), Goal.id.desc())
        rows = (await db.execute(stmt)).scalars().all()
        return [serialize(r) for r in rows]

    async def _get_goal(self, db: AsyncSession, *, owner: str, pk: int) -> Goal:
        row = (await db.execute(sa.select(Goal).where(Goal.id == pk, Goal.owner_hasn_id == owner))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='目标不存在或无权访问')
        return row

    async def get_goal(self, db: AsyncSession, *, owner: str, pk: int) -> dict:
        goal = await self._get_goal(db, owner=owner, pk=pk)
        data = serialize(goal)
        krs = (
            (
                await db.execute(
                    sa
                    .select(GoalKeyResult)
                    .where(GoalKeyResult.goal_id == pk)
                    .order_by(GoalKeyResult.sort.asc(), GoalKeyResult.id.asc())
                )
            )
            .scalars()
            .all()
        )
        data['key_results'] = [serialize(k) for k in krs]
        return data

    async def create_goal(
        self, db: AsyncSession, *, owner: str, data: dict, enterprise_id: int | None = None, dept_id: int | None = None
    ) -> dict:
        row = Goal(owner_hasn_id=owner, **_ownership(enterprise_id, dept_id), **_pick(_GOAL_FIELDS, data))
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_goal(self, db: AsyncSession, *, owner: str, pk: int, data: dict) -> dict:
        row = await self._get_goal(db, owner=owner, pk=pk)
        for k, v in _pick(_GOAL_FIELDS, data).items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_goal(self, db: AsyncSession, *, owner: str, pk: int) -> None:
        row = await self._get_goal(db, owner=owner, pk=pk)
        await db.delete(row)
        await db.flush()

    async def recompute_goal_progress(self, db: AsyncSession, *, owner: str, pk: int) -> dict:
        """派生进度：可量化 KR 达成率均值（§5.5#4 不接受前端直填）。

        target_value 未设定（=0）的 KR **不可度量**，从均值中排除（此前误把 target=0
        当成 100% 达成 → 「未命名指标 100%」假象；与 webui PLANFIX-1 诚实化口径一致：
        未设定不冒充已达成）。无可度量 KR 时进度兜底为 0。
        """
        goal = await self._get_goal(db, owner=owner, pk=pk)
        krs = (await db.execute(sa.select(GoalKeyResult).where(GoalKeyResult.goal_id == pk))).scalars().all()
        ratios = []
        for k in krs:
            target = float(k.target_value or 0)
            if target == 0:
                continue  # 未设定目标值 → 不可度量，不计入均值
            cur = float(k.current_value or 0)
            if k.direction == 'down':
                ratios.append(max(0.0, min(1.0, (2 * target - cur) / target)))
            else:
                ratios.append(max(0.0, min(1.0, cur / target)))
        goal.progress_pct = round(sum(ratios) / len(ratios) * 100) if ratios else 0
        await db.flush()
        return serialize(goal)

    # ── goal_key_result（经 goal 归属）────────────────────────────────────────
    async def create_kr(self, db: AsyncSession, *, owner: str, goal_id: int, data: dict) -> dict:
        await self._get_goal(db, owner=owner, pk=goal_id)  # 归属校验
        row = GoalKeyResult(goal_id=goal_id, **_pick(_KR_FIELDS, data))
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_kr(self, db: AsyncSession, *, owner: str, kr_id: int, data: dict) -> dict:
        row = (await db.execute(sa.select(GoalKeyResult).where(GoalKeyResult.id == kr_id))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='KR 不存在')
        await self._get_goal(db, owner=owner, pk=row.goal_id)  # 归属校验
        for k, v in _pick(_KR_FIELDS, data).items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_kr(self, db: AsyncSession, *, owner: str, kr_id: int) -> None:
        row = (await db.execute(sa.select(GoalKeyResult).where(GoalKeyResult.id == kr_id))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='KR 不存在')
        await self._get_goal(db, owner=owner, pk=row.goal_id)
        await db.delete(row)
        await db.flush()

    # ── plan ──────────────────────────────────────────────────────────────────
    async def list_plans(
        self, db: AsyncSession, *, owner: str, status: str | None = None, goal_id: int | None = None
    ) -> list[dict]:
        stmt = sa.select(Plan).where(Plan.owner_hasn_id == owner)
        if status:
            stmt = stmt.where(Plan.status == status)
        if goal_id is not None:
            stmt = stmt.where(Plan.goal_id == goal_id)
        stmt = stmt.order_by(Plan.sort.asc(), Plan.id.desc())
        return [serialize(r) for r in (await db.execute(stmt)).scalars().all()]

    async def _get_plan(self, db: AsyncSession, *, owner: str, pk: int) -> Plan:
        row = (await db.execute(sa.select(Plan).where(Plan.id == pk, Plan.owner_hasn_id == owner))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='计划不存在或无权访问')
        return row

    async def get_plan(self, db: AsyncSession, *, owner: str, pk: int) -> dict:
        plan = await self._get_plan(db, owner=owner, pk=pk)
        data = serialize(plan)
        ms = (
            (
                await db.execute(
                    sa
                    .select(PlanMilestone)
                    .where(PlanMilestone.plan_id == pk)
                    .order_by(PlanMilestone.sort.asc(), PlanMilestone.id.asc())
                )
            )
            .scalars()
            .all()
        )
        data['milestones'] = [serialize(m) for m in ms]
        return data

    async def create_plan(
        self,
        db: AsyncSession,
        *,
        owner: str,
        data: dict,
        default_bound_agent: str | None = None,
        enterprise_id: int | None = None,
        dept_id: int | None = None,
    ) -> dict:
        fields = _pick(_PLAN_FIELDS, data)
        if 'goal_id' in fields:
            await self._get_goal(db, owner=owner, pk=fields['goal_id'])  # 归属校验：计划只能挂自己的目标
        # 分身经 agent 通道建计划：缺省绑定「调用方分身自己」（default_bound_agent 来自 Agent JWT，不让分身自报）。
        # 分身本就是创建者，不该让它记得把自己 id 填进来；要绑给「别的分身」时才显式传 bound_agent_id 覆盖。
        # owner 经 app(webui) 通道手动建计划不自动绑（default_bound_agent=None，由主人在 UI 显式选协作分身）。
        if default_bound_agent and not (fields.get('bound_agent_id') or '').strip():
            fields['bound_agent_id'] = default_bound_agent
        row = Plan(owner_hasn_id=owner, **_ownership(enterprise_id, dept_id), **fields)
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_plan(self, db: AsyncSession, *, owner: str, pk: int, data: dict) -> dict:
        row = await self._get_plan(db, owner=owner, pk=pk)
        fields = _pick(_PLAN_FIELDS, data)
        if 'goal_id' in fields:
            await self._get_goal(db, owner=owner, pk=fields['goal_id'])
        for k, v in fields.items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_plan(self, db: AsyncSession, *, owner: str, pk: int) -> None:
        row = await self._get_plan(db, owner=owner, pk=pk)
        await db.delete(row)
        await db.flush()

    async def set_plan_bound_agent(self, db: AsyncSession, *, owner: str, pk: int, bound_agent_id: str | None) -> dict:
        """AppCollab 平台标准列：设置/解绑计划的协作分身（doc21 §4.1）。"""
        row = await self._get_plan(db, owner=owner, pk=pk)
        row.bound_agent_id = bound_agent_id
        await db.flush()
        return serialize(row)

    # ── plan_milestone（经 plan 归属）─────────────────────────────────────────
    async def create_milestone(self, db: AsyncSession, *, owner: str, plan_id: int, data: dict) -> dict:
        await self._get_plan(db, owner=owner, pk=plan_id)
        row = PlanMilestone(plan_id=plan_id, **_pick(_MILESTONE_FIELDS, data))
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_milestone(self, db: AsyncSession, *, owner: str, milestone_id: int, data: dict) -> dict:
        row = (await db.execute(sa.select(PlanMilestone).where(PlanMilestone.id == milestone_id))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='里程碑不存在')
        await self._get_plan(db, owner=owner, pk=row.plan_id)
        for k, v in _pick(_MILESTONE_FIELDS, data).items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_milestone(self, db: AsyncSession, *, owner: str, milestone_id: int) -> None:
        row = (await db.execute(sa.select(PlanMilestone).where(PlanMilestone.id == milestone_id))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='里程碑不存在')
        await self._get_plan(db, owner=owner, pk=row.plan_id)
        await db.delete(row)
        await db.flush()

    # ── todo ──────────────────────────────────────────────────────────────────
    async def list_todos(
        self,
        db: AsyncSession,
        *,
        owner: str,
        status: str | None = None,
        actor: str | None = None,
        plan_id: int | None = None,
        goal_id: int | None = None,
    ) -> list[dict]:
        stmt = sa.select(Todo).where(Todo.owner_hasn_id == owner)
        if status:
            stmt = stmt.where(Todo.status == status)
        if actor:
            stmt = stmt.where(Todo.actor == actor)
        if plan_id is not None:
            stmt = stmt.where(Todo.plan_id == plan_id)
        if goal_id is not None:
            stmt = stmt.where(Todo.goal_id == goal_id)
        stmt = stmt.order_by(Todo.priority.desc(), Todo.due_at.asc().nullslast(), Todo.id.desc())
        return [serialize(r) for r in (await db.execute(stmt)).scalars().all()]

    async def _get_todo(self, db: AsyncSession, *, owner: str, pk: int) -> Todo:
        row = (await db.execute(sa.select(Todo).where(Todo.id == pk, Todo.owner_hasn_id == owner))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='待办不存在或无权访问')
        return row

    async def get_todo(self, db: AsyncSession, *, owner: str, pk: int) -> dict:
        return serialize(await self._get_todo(db, owner=owner, pk=pk))

    async def create_todo(
        self, db: AsyncSession, *, owner: str, data: dict, enterprise_id: int | None = None, dept_id: int | None = None
    ) -> dict:
        row = Todo(owner_hasn_id=owner, **_ownership(enterprise_id, dept_id), **_pick(_TODO_FIELDS, data))
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_todo(self, db: AsyncSession, *, owner: str, pk: int, data: dict) -> dict:
        row = await self._get_todo(db, owner=owner, pk=pk)
        for k, v in _pick(_TODO_FIELDS, data).items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_todo(self, db: AsyncSession, *, owner: str, pk: int) -> None:
        row = await self._get_todo(db, owner=owner, pk=pk)
        await db.delete(row)
        await db.flush()

    # ── event（日历单元）────────────────────────────────────────────────────────
    async def list_events(
        self, db: AsyncSession, *, owner: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[dict]:
        stmt = sa.select(Event).where(Event.owner_hasn_id == owner)
        if start is not None:
            stmt = stmt.where(Event.end_at >= start)
        if end is not None:
            stmt = stmt.where(Event.start_at <= end)
        stmt = stmt.order_by(Event.start_at.asc())
        return [serialize(r) for r in (await db.execute(stmt)).scalars().all()]

    async def _get_event(self, db: AsyncSession, *, owner: str, pk: int) -> Event:
        row = (await db.execute(sa.select(Event).where(Event.id == pk, Event.owner_hasn_id == owner))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='日程不存在或无权访问')
        return row

    async def create_event(
        self, db: AsyncSession, *, owner: str, data: dict, enterprise_id: int | None = None, dept_id: int | None = None
    ) -> dict:
        fields = _pick(_EVENT_FIELDS, data)
        if 'todo_id' in fields:
            await self._get_todo(db, owner=owner, pk=fields['todo_id'])  # flex 块只能挂自己的待办
        row = Event(owner_hasn_id=owner, **_ownership(enterprise_id, dept_id), **fields)
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_event(self, db: AsyncSession, *, owner: str, pk: int, data: dict) -> dict:
        row = await self._get_event(db, owner=owner, pk=pk)
        for k, v in _pick(_EVENT_FIELDS, data).items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_event(self, db: AsyncSession, *, owner: str, pk: int) -> None:
        row = await self._get_event(db, owner=owner, pk=pk)
        await db.delete(row)
        await db.flush()

    async def reschedule_event(
        self, db: AsyncSession, *, owner: str, pk: int, start_at: datetime, end_at: datetime, lock: bool = True
    ) -> dict:
        """拖动改期：更新时间块时间并按 Motion 语义自动锁定（设计 §8.1 不变量 #2）。"""
        row = await self._get_event(db, owner=owner, pk=pk)
        if end_at <= start_at:
            raise errors.RequestError(msg='结束时间必须晚于开始时间')
        row.start_at = start_at
        row.end_at = end_at
        if lock:
            row.locked = True
        await db.flush()
        return serialize(row)

    # ── habit ───────────────────────────────────────────────────────────────────
    async def list_habits(self, db: AsyncSession, *, owner: str, status: str | None = None) -> list[dict]:
        stmt = sa.select(Habit).where(Habit.owner_hasn_id == owner)
        if status:
            stmt = stmt.where(Habit.status == status)
        stmt = stmt.order_by(Habit.id.desc())
        return [serialize(r) for r in (await db.execute(stmt)).scalars().all()]

    async def _get_habit(self, db: AsyncSession, *, owner: str, pk: int) -> Habit:
        row = (await db.execute(sa.select(Habit).where(Habit.id == pk, Habit.owner_hasn_id == owner))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='习惯不存在或无权访问')
        return row

    async def create_habit(
        self, db: AsyncSession, *, owner: str, data: dict, enterprise_id: int | None = None, dept_id: int | None = None
    ) -> dict:
        row = Habit(owner_hasn_id=owner, **_ownership(enterprise_id, dept_id), **_pick(_HABIT_FIELDS, data))
        db.add(row)
        await db.flush()
        return serialize(row)

    async def update_habit(self, db: AsyncSession, *, owner: str, pk: int, data: dict) -> dict:
        row = await self._get_habit(db, owner=owner, pk=pk)
        for k, v in _pick(_HABIT_FIELDS, data).items():
            setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def delete_habit(self, db: AsyncSession, *, owner: str, pk: int) -> None:
        row = await self._get_habit(db, owner=owner, pk=pk)
        await db.delete(row)
        await db.flush()

    async def checkin_habit(self, db: AsyncSession, *, owner: str, habit_id: int, data: dict) -> dict:
        """打卡（一天一卡，UNIQUE 去重）并刷新 streak/best_streak。"""
        habit = await self._get_habit(db, owner=owner, pk=habit_id)
        fields = _pick(_CHECKIN_FIELDS, data)
        if 'checkin_date' not in fields:
            raise errors.RequestError(msg='checkin_date 必填')
        existing = (
            (
                await db.execute(
                    sa.select(HabitCheckin).where(
                        HabitCheckin.habit_id == habit_id, HabitCheckin.checkin_date == fields['checkin_date']
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            db.add(HabitCheckin(habit_id=habit_id, **fields))
            await db.flush()
        count = (
            await db.execute(
                sa.select(sa.func.count()).select_from(HabitCheckin).where(HabitCheckin.habit_id == habit_id)
            )
        ).scalar() or 0
        habit.streak = int(count)
        habit.best_streak = max(int(habit.best_streak or 0), habit.streak)
        await db.flush()
        return serialize(habit)

    # ── preference（owner 单例 upsert）──────────────────────────────────────────
    async def get_preference(self, db: AsyncSession, *, owner: str) -> dict:
        row = (await db.execute(sa.select(Preference).where(Preference.owner_hasn_id == owner))).scalars().first()
        if not row:
            return {}
        return serialize(row)

    async def upsert_preference(self, db: AsyncSession, *, owner: str, data: dict) -> dict:
        row = (await db.execute(sa.select(Preference).where(Preference.owner_hasn_id == owner))).scalars().first()
        fields = _pick(_PREFERENCE_FIELDS, data)
        if row is None:
            row = Preference(owner_hasn_id=owner, **fields)
            db.add(row)
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        await db.flush()
        return serialize(row)

    async def claim_proactive_planning(self, db: AsyncSession, *, owner: str) -> bool:
        """主动规划闭环「恰好一次」原子认领（KNOWU §7，Open Q#3 事件驱动 + 幂等标记）。

        5 维画像首次全 sufficient 时，分身从「被动」切「主动」需触发一次主动规划工作会话。
        多设备/多次刷新可能并发触发，这里用 `preference` owner 单例行的 `proactive_planned`
        作跨设备持久幂等标记：原子 `INSERT ... ON CONFLICT DO UPDATE ... WHERE proactive_planned=false`
        —— 行不存在则插入并认领；行已存在且标记 false 则更新认领；标记已为 true 则 WHERE 不命中、
        不返回任何行。`RETURNING id` 是否有行即「本次是否认领成功」，保证全局只有一方赢。

        依赖 `uq_plan_preference_owner` 唯一索引（迁移 2026-06-27 已建）作 ON CONFLICT 目标。
        返回 True=本次认领成功（调用方应触发主动规划）；False=此前已认领（幂等跳过）。
        """
        stmt = sa.text(
            """
            INSERT INTO hasn_plan.preference (owner_hasn_id, proactive_planned, created_time, updated_time)
            VALUES (:owner, true, now(), now())
            ON CONFLICT (owner_hasn_id)
            DO UPDATE SET proactive_planned = true, updated_time = now()
            WHERE hasn_plan.preference.proactive_planned = false
            RETURNING id
            """
        )
        claimed_id = (await db.execute(stmt, {'owner': owner})).scalar()
        await db.flush()
        return claimed_id is not None

    # ── 「今日」聚合（设计 §10.2 首屏）──────────────────────────────────────────
    async def today_overview(self, db: AsyncSession, *, owner: str, day_start: datetime, day_end: datetime) -> dict:
        """今日首屏数据：当日时间块 + 需主人/分身分流的待办 + 目标进度环。"""
        events = await self.list_events(db, owner=owner, start=day_start, end=day_end)
        scheduled_todos = await self.list_todos(db, owner=owner, status='scheduled')
        inbox = await self.list_todos(db, owner=owner, status='inbox')
        all_todos = await self.list_todos(db, owner=owner)
        agent_queue = [t for t in all_todos if t['actor'] == 'agent' and t['status'] in ('doing', 'waiting_review')]
        goals = await self.list_goals(db, owner=owner, status='active')
        return {
            'events': events,
            'scheduled_todos': scheduled_todos,
            'inbox': inbox,
            'agent_queue': agent_queue,
            'goals': goals,
        }


plan_service = PlanService()
