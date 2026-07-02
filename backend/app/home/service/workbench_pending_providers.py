"""各 AI-Native 应用的「未处理项」provider（设计 doc 05 §3）。

每个 provider 是对某应用**现成只读 service** 的薄包装：owner 隔离、零 fake、把结果映射成
统一的 `PendingItem`。provider 只产 canonical `/apps/<id>...` 深链（后端直接产权威路由，
无需归一化——归一化只对付主脑写的外部输入）。

新增应用 = 加一个 async provider 函数 + 注册进 `PENDING_PROVIDERS`，聚合器自动纳入。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

from backend.app.hasn_community.service.notification_service import (
    notification_service as community_notification_service,
)
from backend.app.hasn_deck.service.deck_service import deck_service
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.hasn_reel.service.reel_service import reel_service
from backend.app.hasn_studio.service.studio_service import studio_service
from backend.app.hasn_task.service.agent_task_service import agent_task_service
from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
from backend.app.home.schema.workbench_pending import PendingItem
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# provider 签名：给定 owner + 每应用条数上限，返回该应用的未处理项（已映射为 PendingItem）。
PendingProviderFn = Callable[..., Awaitable[list[PendingItem]]]


def _epoch_ms(dt: datetime) -> int | None:
    """tz-aware / naive datetime → ms epoch（naive 按项目时区兜底）。"""
    try:
        return int(dt.timestamp() * 1000)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso(value: object) -> datetime | None:
    """serialize 出来的 ISO 字符串 → datetime；非法/空返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _pick_by_status(
    rows: list[dict],
    *,
    app_id: str,
    category: str,
    status_map: dict[str, tuple[str, str]],
    deep_link: Callable[[object], str],
    id_key: str = 'id',
    title_key: str = 'title',
    title_default: str = '未命名',
) -> list[PendingItem]:
    """行列表 → 命中 status_map（status→(urgency, hint)）的行映射为 PendingItem，其余状态跳过。

    列表类应用共用：service 现成 list 方法返回全量行，本函数按业务态挑「未处理」并映射。
    """
    items: list[PendingItem] = []
    for row in rows:
        spec = status_map.get(row.get('status'))
        if spec is None:
            continue
        urgency, hint = spec
        rid = row.get(id_key)
        items.append(
            PendingItem(
                app_id=app_id,
                category=category,  # type: ignore[arg-type]
                urgency=urgency,  # type: ignore[arg-type]
                title=row.get(title_key) or title_default,
                summary=hint,
                ref=f'{app_id}:{rid}' if rid is not None else app_id,
                deep_link=deep_link(rid),
                occurred_at=None,
            )
        )
    return items


# ── task：待审批 / 待装技能的任务（周期任务的业务态，需主人处理）──────────────────
# 业务态字典见 hasn_task/model/task.py：pending_approval:待审批 / needs_skill_install:待安装技能。
_TASK_UNPROCESSED = (
    ('pending_approval', 'high', '分身建的周期任务待你审批'),
    ('needs_skill_install', 'medium', '任务缺技能，待安装后可运行'),
)


async def task_pending_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    items: list[PendingItem] = []
    for state, urgency, hint in _TASK_UNPROCESSED:
        rows = await agent_task_service.list_tasks(db, owner_id=owner_hasn_id, state=state, limit=limit)
        for row in rows:
            task_id = row.get('task_id')
            items.append(
                PendingItem(
                    app_id='task',
                    category='task',
                    urgency=urgency,  # type: ignore[arg-type]
                    title=row.get('name') or '未命名任务',
                    summary=hint,
                    ref=f'task:{task_id}' if task_id else f'task:{row.get("name")}',
                    deep_link=f'/apps/tasks/{task_id}' if task_id else '/apps/tasks',
                    occurred_at=None,
                )
            )
    return items[:limit]


# ── plan：**只扫逾期**待办（未逾期 / 已自动派发的走 PLAN-TRIAGE，不在简报重复）────────
# 福仔铁律：日程待办已有自动派发逻辑，简报的待办扫描**只扫逾期**（due_at < now 且未完成）。
_TODO_ACTIVE = ('pending', 'in_progress')


async def plan_overdue_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    now = timezone.now()
    now_ms = _epoch_ms(now)
    items: list[PendingItem] = []
    for status in _TODO_ACTIVE:
        rows = await plan_service.list_todos(db, owner=owner_hasn_id, status=status)
        for row in rows:
            due = _parse_iso(row.get('due_at'))
            due_ms = _epoch_ms(due) if due else None
            # 只保留逾期：有 due 且已过期。无 due / 未来 due 一律跳过。
            if due_ms is None or now_ms is None or due_ms >= now_ms:
                continue
            todo_id = row.get('id')
            items.append(
                PendingItem(
                    app_id='plan',
                    category='plan',
                    urgency='high',  # 逾期即高优先
                    title=row.get('title') or '未命名待办',
                    summary=row.get('notes') or '待办已逾期',
                    ref=f'todo:{todo_id}',
                    deep_link='/apps/plan',
                    occurred_at=due_ms,
                )
            )
    # 逾期越久越靠前（due 越早）
    items.sort(key=lambda it: it.occurred_at or 0)
    return items[:limit]


# ── community：未读社区通知（社交类，主人可去通知页处理）─────────────────────────
# 复用统一通知 service 的 recipient_hasn_id 口径（=owner_hasn_id，零适配）。deep_link 一律
# canonical `/apps/community/notifications`（不用通知自带 link——它可能是 hasn:// URI）。
async def community_notification_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    result = await community_notification_service.list_notifications(
        db, recipient_hasn_id=owner_hasn_id, unread_only=True, limit=limit
    )
    items: list[PendingItem] = []
    for n in result.get('items', []):
        nid = n.get('id')
        created = _parse_iso(n.get('created_time'))
        items.append(
            PendingItem(
                app_id='community',
                category='social',
                urgency='low',  # 未读通知信息类，低优先；主脑分诊时可覆盖
                title=n.get('title') or '社区通知',
                summary=n.get('preview'),
                ref=f'notification:{nid}' if nid is not None else 'notification',
                deep_link='/apps/community/notifications',
                occurred_at=_epoch_ms(created) if created else None,
            )
        )
    return items[:limit]


# ── workflow：待审批工作流（分身建的定时图，需主人审批；顶层路由 /workflows/{id}）──────
# 业务态见 hasn_task/model/workflow.py：pending_approval 需主人审批（对齐 task）。
# 深链用 workflow_id（=workflow_uuid，端云稳定权威 id，非本地 id）。
_WORKFLOW_UNPROCESSED = {'pending_approval': ('high', '分身建的工作流待你审批')}


async def workflow_pending_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    rows = await agent_workflow_service.list_workflows(db, owner_id=owner_hasn_id)
    items = _pick_by_status(
        rows,
        app_id='workflow',
        category='task',
        status_map=_WORKFLOW_UNPROCESSED,
        deep_link=lambda i: f'/workflows/{i}' if i else '/workflows',
        id_key='workflow_id',
        title_key='name',
        title_default='未命名工作流',
    )
    return items[:limit]


# ── deck：草稿演示待完善（低优先提醒；详情路由 /apps/deck/{id} 用云端权威 deck id）────────
_DECK_UNPROCESSED = {'draft': ('low', '有演示草稿待完善')}


async def deck_pending_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    result = await deck_service.list_decks(db, owner_id=owner_hasn_id, limit=50)
    items = _pick_by_status(
        result.get('items', []),
        app_id='deck',
        category='app',
        status_map=_DECK_UNPROCESSED,
        deep_link=lambda i: f'/apps/deck/{i}' if i else '/apps/deck',
        title_default='未命名演示',
    )
    return items[:limit]


# ── reel：短视频创作等你回答 / 失败待处理（无 creation 详情路由 → 退回应用入口 /apps/reel）──
_REEL_UNPROCESSED = {
    'waiting_user': ('high', '短视频创作等你回答'),
    'failed': ('medium', '短视频创作失败待处理'),
}


async def reel_pending_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    rows = await reel_service.list_creations(db, owner_hasn_id=owner_hasn_id)
    items = _pick_by_status(
        rows,
        app_id='reel',
        category='app',
        status_map=_REEL_UNPROCESSED,
        deep_link=lambda _i: '/apps/reel',
        title_default='未命名创作',
    )
    return items[:limit]


# ── studio：视频成品待审核 / 渲染失败待处理（无 artifact 详情路由 → 退回应用入口 /apps/studio）─
_STUDIO_UNPROCESSED = {
    'reviewing': ('medium', '视频成品待审核'),
    'failed': ('medium', '视频渲染失败待处理'),
}


async def studio_pending_provider(db: AsyncSession, *, owner_hasn_id: str, limit: int) -> list[PendingItem]:
    rows = await studio_service.list_artifacts(db, owner_hasn_id=owner_hasn_id)
    items = _pick_by_status(
        rows,
        app_id='studio',
        category='app',
        status_map=_STUDIO_UNPROCESSED,
        deep_link=lambda _i: '/apps/studio',
        title_default='未命名成品',
    )
    return items[:limit]


# ── 注册表（新增应用在此加一行；聚合器按 key 顺序读取）──────────────────────────────
# M1 起步 task + plan（owner 口径均为 owner_hasn_id，零适配）。M3 横向补齐：community（未读
# 通知）+ workflow/deck/reel/studio（口径均 owner_id/owner_hasn_id，str，零适配）。
# creator（走 owner_id bigint，需 hasn_id→user_id 适配层）+ quant（回测无 list 方法）留后续。
PENDING_PROVIDERS: dict[str, PendingProviderFn] = {
    'task': task_pending_provider,
    'plan': plan_overdue_provider,
    'community': community_notification_provider,
    'workflow': workflow_pending_provider,
    'deck': deck_pending_provider,
    'reel': reel_pending_provider,
    'studio': studio_pending_provider,
}
