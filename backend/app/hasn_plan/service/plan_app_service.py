"""规划与目标管理（app_id=plan）统一业务 service（owner 隔离的权威实现）。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §5。

与 designsystem 同范式：codegen 生成的泛型 per-table service/api 留盘不接线（引用 user_id，
与本应用 owner_hasn_id/HASN 身份模型不兼容）；真实业务面由本 service + 自定义 app/agent API 承载。

owner 隔离铁律（设计 §5.5#2）：所有读写按 `owner_hasn_id` 过滤，**owner 身份永远由调用方
（Owner JWT / Agent JWT claims）解析后传入，绝不接受请求体携带的身份**。子对象（KR/里程碑/打卡）
经父对象归属校验，间接落到 owner。`progress_pct` 派生缓存不接受客户端直填（§5.5#4）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_plan.model import (
    Event,
    EventAttendee,
    Goal,
    GoalKeyResult,
    Habit,
    HabitCheckin,
    Plan,
    PlanMilestone,
    Preference,
    Todo,
)
from backend.app.hasn_plan.service.plan_authz import (
    PlanEnterpriseScope,
    active_enterprise_id,
    enterprise_event_who_filter,
    resolve_plan_enterprise_scope,
)
from backend.app.hasn_plan.service.plan_visibility import apply_event_visibility, redact_event_to_busy
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
    # PLAN-TRIAGE 留痕三列（独立于 notes 用户备注）：owner_decision 决策留痕 / 完成结论 / 放弃原因。
    'decision_note',
    'completion_note',
    'cancel_reason',
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
    # OA→plan 反向锚（[04] §6.3）：oa:room_booking:{id} / oa:interview:{id}，服务端注入、非派生。
    'origin_ref',
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
        # 个人空间读恒 enterprise_id IS NULL（[04] §5.1 空间分叉；企业目标经 list_enterprise_* 独立读，不混入）。
        stmt = sa.select(Goal).where(Goal.owner_hasn_id == owner, Goal.enterprise_id.is_(None))
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
        # 个人空间读恒 enterprise_id IS NULL（[04] §5.1；企业计划/团队 OKR 首期不 surface，PE-D1）。
        stmt = sa.select(Plan).where(Plan.owner_hasn_id == owner, Plan.enterprise_id.is_(None))
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
        # 个人空间读恒 enterprise_id IS NULL（[04] §5.1；企业待办经 list_enterprise_todos 独立 scope 读）。
        stmt = sa.select(Todo).where(Todo.owner_hasn_id == owner, Todo.enterprise_id.is_(None))
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

    async def list_enterprise_todos(
        self,
        db: AsyncSession,
        *,
        viewer_owner_hasn_id: str,
        enterprise_id: int,
        status: str | None = None,
        scope: PlanEnterpriseScope | None = None,
    ) -> list[dict]:
        """企业空间待办读（[04] §5.1）：恒前置 enterprise_id==E + WHO 数据范围（可见成员的待办）。

        - 非本企业成员 → 空（企业隔离硬底线，冻结不变量 #2）；
        - 仅返回数据范围内可见成员（``visible_member_hasn_ids``，恒含自己）所属的企业待办——超数据范围者不返回。

        与个人 ``list_todos`` 是两次独立 scope-read（[04] 不变量 #2），调用方（today_overview 企业分支）
        合并，不混一条查询。待办无 event 的忙闲/可见性两轴，故只按 WHO 数据范围过滤、不做 WHAT 裁剪。
        """
        if scope is None:
            scope = await resolve_plan_enterprise_scope(
                db, viewer_owner_hasn_id=viewer_owner_hasn_id, enterprise_id=enterprise_id
            )
        if not scope.is_member or not scope.visible_member_hasn_ids:
            return []
        stmt = sa.select(Todo).where(
            Todo.enterprise_id == enterprise_id,
            Todo.owner_hasn_id.in_(scope.visible_member_hasn_ids),
        )
        if status:
            stmt = stmt.where(Todo.status == status)
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
        # 个人空间读恒 enterprise_id IS NULL（[04] §5.1；企业日历经 list_enterprise_events 独立 scope 读裁剪）。
        stmt = sa.select(Event).where(Event.owner_hasn_id == owner, Event.enterprise_id.is_(None))
        if start is not None:
            stmt = stmt.where(Event.end_at >= start)
        if end is not None:
            stmt = stmt.where(Event.start_at <= end)
        stmt = stmt.order_by(Event.start_at.asc())
        return [serialize(r) for r in (await db.execute(stmt)).scalars().all()]

    async def list_enterprise_events(
        self,
        db: AsyncSession,
        *,
        viewer_owner_hasn_id: str,
        enterprise_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
        scope: PlanEnterpriseScope | None = None,
    ) -> list[dict]:
        """企业空间事件读（[04] §4/§5.1）：恒前置 enterprise_id==E + WHO 数据范围过滤 + WHAT 忙闲裁剪。

        - 非本企业成员 → 返回空（企业隔离硬底线，冻结不变量 #2）；
        - 仅返回数据范围内可见 / 企业公开 / 我被邀 / 显式共享给我的事件，超数据范围者连「忙」都不返回（PE-D2）；
        - 每条按 WHAT 轴裁剪：自己 / 被邀 / 公开 / 被共享 → 全详情；数据范围内同事私有 → 仅忙闲块（隐藏标题）。

        个人「今日」与企业「今日」是**两次独立 scope-read 的并集**（[04] 不变量 #2）——本方法只查企业侧，
        个人侧仍走 ``list_events``，调用方（工具面 / today_overview 企业分支）合并，不混一条查询。
        """
        if scope is None:
            scope = await resolve_plan_enterprise_scope(
                db, viewer_owner_hasn_id=viewer_owner_hasn_id, enterprise_id=enterprise_id
            )
        if not scope.is_member:
            return []
        stmt = sa.select(Event).where(Event.enterprise_id == enterprise_id)
        if start is not None:
            stmt = stmt.where(Event.end_at >= start)
        if end is not None:
            stmt = stmt.where(Event.start_at <= end)
        stmt = stmt.where(enterprise_event_who_filter(scope)).order_by(Event.start_at.asc())
        rows = (await db.execute(stmt)).scalars().all()
        return [
            apply_event_visibility(
                serialize(r),
                viewer_hasn_id=viewer_owner_hasn_id,
                is_attendee=r.id in scope.attendee_event_ids,
                is_shared=r.id in scope.shared_event_ids,
            )
            for r in rows
        ]

    async def _get_event(self, db: AsyncSession, *, owner: str, pk: int) -> Event:
        row = (await db.execute(sa.select(Event).where(Event.id == pk, Event.owner_hasn_id == owner))).scalars().first()
        if not row:
            raise errors.NotFoundError(msg='日程不存在或无权访问')
        return row

    async def create_event(
        self,
        db: AsyncSession,
        *,
        owner: str,
        data: dict,
        enterprise_id: int | None = None,
        dept_id: int | None = None,
        attendees: list[str] | None = None,
    ) -> dict:
        fields = _pick(_EVENT_FIELDS, data)
        if 'todo_id' in fields:
            await self._get_todo(db, owner=owner, pk=fields['todo_id'])  # flex 块只能挂自己的待办
        row = Event(owner_hasn_id=owner, **_ownership(enterprise_id, dept_id), **fields)
        db.add(row)
        await db.flush()
        # 企业事件：服务端自动展开参会人（组织者行 + 受邀行）——不变量 #4「参会人仅企业事件」（[04] §6.3）。
        # 个人事件（enterprise_id 为 None）不建参会行（EventAttendee 冗余 enterprise_id NOT NULL）。
        if enterprise_id is not None:
            await self._seed_event_attendees(
                db, event_id=int(row.id), enterprise_id=enterprise_id, organizer=owner, attendees=attendees or []
            )
        return serialize(row)

    # ── event attendee（企业会议参会人 RSVP，[04] §6.2/§6.3）──────────────────────────
    async def _seed_event_attendees(
        self, db: AsyncSession, *, event_id: int, enterprise_id: int, organizer: str, attendees: list[str]
    ) -> None:
        """企业事件建成后展开参会行：发起人 organizer/accepted + 其余 required/none（去重、跳过组织者本人）。"""
        db.add(
            EventAttendee(
                event_id=event_id,
                enterprise_id=enterprise_id,
                attendee_hasn_id=organizer,
                role='organizer',
                rsvp='accepted',
                responded_at=datetime.now(timezone.utc),
            )
        )
        seen = {organizer}
        for raw in attendees:
            h = str(raw or '').strip()
            if not h or h in seen:
                continue
            seen.add(h)
            db.add(
                EventAttendee(
                    event_id=event_id, enterprise_id=enterprise_id, attendee_hasn_id=h, role='required', rsvp='none'
                )
            )
        await db.flush()

    async def _get_enterprise_event(self, db: AsyncSession, *, owner: str, pk: int) -> Event:
        """取「自己组织的企业事件」（invite/减人授权：仅组织者=事件 owner 可管理参会人）。"""
        row = await self._get_event(db, owner=owner, pk=pk)
        if row.enterprise_id is None:
            raise errors.RequestError(msg='个人日程无参会人，无法管理参会名单')
        return row

    async def list_attendees(self, db: AsyncSession, *, event_id: int) -> list[dict]:
        rows = (
            (
                await db.execute(
                    sa.select(EventAttendee).where(EventAttendee.event_id == event_id).order_by(EventAttendee.id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [serialize(r) for r in rows]

    async def invite_attendees(
        self,
        db: AsyncSession,
        *,
        owner: str,
        event_id: int,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        default_role: str = 'required',
    ) -> dict:
        """组织者加/减参会人（[04] §6.2）：仅事件 owner 可调；组织者行不可移除。返回 added/removed + 最新名单。"""
        ev = await self._get_enterprise_event(db, owner=owner, pk=event_id)
        existing = {
            r.attendee_hasn_id: r
            for r in (await db.execute(sa.select(EventAttendee).where(EventAttendee.event_id == event_id)))
            .scalars()
            .all()
        }
        added: list[str] = []
        for raw in add or []:
            h = str(raw or '').strip()
            if not h or h in existing or h == ev.owner_hasn_id:
                continue
            db.add(
                EventAttendee(
                    event_id=event_id,
                    enterprise_id=ev.enterprise_id,
                    attendee_hasn_id=h,
                    role=default_role,
                    rsvp='none',
                )
            )
            existing[h] = None  # 占位防同批重复
            added.append(h)
        removed: list[str] = []
        for raw in remove or []:
            h = str(raw or '').strip()
            if not h or h == ev.owner_hasn_id:
                continue  # 组织者本人不可移除
            r = existing.get(h)
            if r is not None and r.role != 'organizer':
                await db.delete(r)
                removed.append(h)
        await db.flush()
        return {
            'event_id': event_id,
            'enterprise_id': ev.enterprise_id,
            'added': added,
            'removed': removed,
            'attendees': await self.list_attendees(db, event_id=event_id),
        }

    async def respond_rsvp(self, db: AsyncSession, *, owner: str, event_id: int, rsvp: str) -> dict:
        """参会人回复 RSVP（[04] §6.2）：仅本人参会行可改；值限 accepted/declined/tentative。"""
        val = str(rsvp or '').strip().lower()
        if val not in ('accepted', 'declined', 'tentative'):
            raise errors.RequestError(msg='RSVP 值非法（accepted/declined/tentative）')
        row = (
            (
                await db.execute(
                    sa.select(EventAttendee).where(
                        EventAttendee.event_id == event_id, EventAttendee.attendee_hasn_id == owner
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise errors.NotFoundError(msg='你不是该会议的参会人，无法回复')
        row.rsvp = val
        row.responded_at = datetime.now(timezone.utc)
        await db.flush()
        return serialize(row)

    async def member_availability(
        self,
        db: AsyncSession,
        *,
        viewer_owner_hasn_id: str,
        enterprise_id: int,
        member_hasn_ids: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, list[dict]]:
        """查企业成员忙闲找空档（[04] §6.2）：受 A3 数据范围约束（只回可见成员），**只回忙闲块、不回标题**。

        返回 ``{member_hasn_id: [忙闲块...]}``；非成员 / 无可见目标 → 空 dict。所有块经 ``redact_event_to_busy``
        统一裁成匿名「忙碌」块（调度用途，最保守隐私）。
        """
        scope = await resolve_plan_enterprise_scope(
            db, viewer_owner_hasn_id=viewer_owner_hasn_id, enterprise_id=enterprise_id
        )
        if not scope.is_member:
            return {}
        visible = scope.visible_member_hasn_ids
        targets = [m for m in dict.fromkeys(member_hasn_ids) if m in visible]  # 去重保序 + A3 可见性约束
        if not targets:
            return {}
        stmt = sa.select(Event).where(Event.enterprise_id == enterprise_id, Event.owner_hasn_id.in_(targets))
        if start is not None:
            stmt = stmt.where(Event.end_at >= start)
        if end is not None:
            stmt = stmt.where(Event.start_at <= end)
        stmt = stmt.order_by(Event.start_at.asc())
        rows = (await db.execute(stmt)).scalars().all()
        out: dict[str, list[dict]] = {m: [] for m in targets}
        for r in rows:
            out.setdefault(r.owner_hasn_id, []).append(redact_event_to_busy(serialize(r)))
        return out

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
        # 个人空间读恒 enterprise_id IS NULL（[04] §5.1；团队习惯首期不 surface，PE-D1）。
        stmt = sa.select(Habit).where(Habit.owner_hasn_id == owner, Habit.enterprise_id.is_(None))
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

    async def _claim_periodic(
        self, db: AsyncSession, *, owner: str, column: str, cooldown_days: int
    ) -> bool:
        """周期性「超冷却期才可重新认领」原子闸（KNOWU「每日关注·了解主人」§每周再提醒）。

        每日简报每天都跑，采访 / 成长会话不能每天派。用 `preference.<column>`（时间戳）记「上次派发时间」，
        原子 `INSERT ... ON CONFLICT DO UPDATE ... WHERE <上次派发为空 OR 已超 cooldown_days 天> RETURNING id`：
        - 行不存在 → 插入并认领（首次）；
        - 行存在且从未派过（NULL）或距上次 > cooldown_days 天 → 更新时间戳并认领；
        - 行存在且冷却期内 → WHERE 不命中、无行更新、RETURNING 空 → 不认领。
        多设备并发也只赢一方（依赖 `uq_plan_preference_owner` 唯一索引作 ON CONFLICT 目标）。
        `column` 由调用方硬编码传入（非用户输入），无注入风险。返回 True=本次认领成功（调用方应派会话）。
        """
        stmt = sa.text(
            f"""
            INSERT INTO hasn_plan.preference (owner_hasn_id, {column}, created_time, updated_time)
            VALUES (:owner, now(), now(), now())
            ON CONFLICT (owner_hasn_id)
            DO UPDATE SET {column} = now(), updated_time = now()
            WHERE hasn_plan.preference.{column} IS NULL
               OR hasn_plan.preference.{column} < now() - make_interval(days => :days)
            RETURNING id
            """
        )
        claimed_id = (await db.execute(stmt, {'owner': owner, 'days': int(cooldown_days)})).scalar()
        await db.flush()
        return claimed_id is not None

    async def claim_profile_onboarding(self, db: AsyncSession, *, owner: str, cooldown_days: int = 7) -> bool:
        """「了解主人」采访会话节奏闸：画像不完整时，距上次采访 > cooldown_days 天才再派一次。"""
        return await self._claim_periodic(
            db, owner=owner, column='last_onboarding_at', cooldown_days=cooldown_days
        )

    async def claim_growth_review(self, db: AsyncSession, *, owner: str, cooldown_days: int = 7) -> bool:
        """「成长复盘 / 主动规划」会话节奏闸：画像完整后，每 cooldown_days 天派一次陪主人成长的会话。"""
        return await self._claim_periodic(
            db, owner=owner, column='last_growth_at', cooldown_days=cooldown_days
        )

    # ── 「今日」聚合（设计 §10.2 首屏）──────────────────────────────────────────
    async def today_overview(self, db: AsyncSession, *, owner: str, day_start: datetime, day_end: datetime) -> dict:
        """今日首屏数据：当日时间块 + 需主人/分身分流的待办 + 目标进度环。

        双模（PLAN-ENT B2，[04] §5.1 不变量 #2）：个人组恒返回（owner 隔离，个人零破坏）；主人有活跃企业 E 时
        **另起一条独立 scope-read** 返回企业组（`enterprise_id=E` + 数据范围 WHO/WHAT），置于 ``enterprise`` 子对象
        供 webui 合并显示——**两条各自 scope 读的并集，不是一条混合查询**。无活跃企业 → ``enterprise=None``（纯个人）。
        """
        events = await self.list_events(db, owner=owner, start=day_start, end=day_end)
        scheduled_todos = await self.list_todos(db, owner=owner, status='scheduled')
        inbox = await self.list_todos(db, owner=owner, status='inbox')
        all_todos = await self.list_todos(db, owner=owner)
        agent_queue = [t for t in all_todos if t['actor'] == 'agent' and t['status'] in ('doing', 'waiting_review')]
        goals = await self.list_goals(db, owner=owner, status='active')
        enterprise = await self._today_enterprise_group(db, owner=owner, day_start=day_start, day_end=day_end)
        return {
            'events': events,
            'scheduled_todos': scheduled_todos,
            'inbox': inbox,
            'agent_queue': agent_queue,
            'goals': goals,
            # 企业组（活跃企业空间才有值；webui 合并显示，个人组永不受影响）。
            'enterprise': enterprise,
        }

    async def _today_enterprise_group(
        self, db: AsyncSession, *, owner: str, day_start: datetime, day_end: datetime
    ) -> dict | None:
        """企业「今日」组（B2 空间分叉的企业侧独立 scope-read）：主人无活跃企业 / 非成员 → None。

        活跃企业从 [01] ``hasn_owner_workbench_pref.active_enterprise_id`` 取（分身以主人身份读，owner=主人 hasn_id）。
        企业事件经 WHO/WHAT 裁剪（``list_enterprise_events``），企业待办经 WHO 数据范围（``list_enterprise_todos``）；
        `scope` 一次解析、两读复用（省一次成员/数据范围解析）。团队目标/习惯首期不 surface（PE-D1，列在功能后做）。
        """
        eid = await active_enterprise_id(db, owner)
        if not eid:
            return None
        scope = await resolve_plan_enterprise_scope(db, viewer_owner_hasn_id=owner, enterprise_id=eid)
        if not scope.is_member:
            return None
        events = await self.list_enterprise_events(
            db, viewer_owner_hasn_id=owner, enterprise_id=eid, start=day_start, end=day_end, scope=scope
        )
        ent_todos = await self.list_enterprise_todos(
            db, viewer_owner_hasn_id=owner, enterprise_id=eid, scope=scope
        )
        scheduled_todos = [t for t in ent_todos if t['status'] == 'scheduled']
        inbox = [t for t in ent_todos if t['status'] == 'inbox']
        agent_queue = [t for t in ent_todos if t['actor'] == 'agent' and t['status'] in ('doing', 'waiting_review')]
        return {
            'enterprise_id': eid,
            'events': events,
            'scheduled_todos': scheduled_todos,
            'inbox': inbox,
            'agent_queue': agent_queue,
        }


plan_service = PlanService()
