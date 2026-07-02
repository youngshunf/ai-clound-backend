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

from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.hasn_task.service.agent_task_service import agent_task_service
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


# ── 注册表（新增应用在此加一行；聚合器按 key 顺序读取）──────────────────────────────
# M1 起步 task + plan（owner 口径均为 owner_hasn_id，零适配）；growth/creator/deck… 等
# 走 owner_id(bigint) 的遗留应用需 hasn_id→user_id 适配层，见 doc05 §5 M3。
PENDING_PROVIDERS: dict[str, PendingProviderFn] = {
    'task': task_pending_provider,
    'plan': plan_overdue_provider,
}
