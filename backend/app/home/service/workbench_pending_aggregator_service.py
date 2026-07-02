"""工作台「未处理项」聚合器（设计 doc 05 §3）。

把主人名下各 AI-Native 应用的未处理项聚合成一份**权威、不漏**的清单。每个 provider
独立 try/except 隔离——单个应用读失败只进 `degraded` 名单（如实标注），绝不为其造项、
绝不让整次扫描崩掉（零 fake）。

⚠️ 并发安全：同一个 `AsyncSession` **不能并发查询**（SQLAlchemy AsyncSession 非并发安全）。
故 provider **顺序执行**——M1 只有 task/plan 两个，顺序开销可忽略；未来若需真并发，须给每个
provider 独立开 session（doc05 §5 记档），不能对共享 db 用 asyncio.gather。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.home.schema.workbench_pending import (
    AppPendingGroup,
    PendingScanResult,
)
from backend.app.home.service.workbench_pending_providers import PENDING_PROVIDERS
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 每应用返回明细的默认上限（count 仍是真实总数，items 是前 N 条）。
_DEFAULT_LIMIT_PER_APP = 5


class WorkbenchPendingAggregator:
    """按 owner 扫描各应用未处理项，聚合成结构化结果供主脑分诊。"""

    async def scan(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        apps: list[str] | None = None,
        limit_per_app: int = _DEFAULT_LIMIT_PER_APP,
    ) -> PendingScanResult:
        """扫描 owner 名下 `apps`（缺省=全部已注册应用）的未处理项。

        Args:
            db: 数据库会话（provider 顺序复用同一会话，勿并发）。
            owner_hasn_id: 主人 hasn_id（owner 隔离，权威口径）。
            apps: 限定扫描的应用 id 列表；None=全部注册 provider。未知 id 静默忽略。
            limit_per_app: 每应用返回明细条数上限（≥1）。
        """
        limit = max(1, limit_per_app)
        selected = apps or list(PENDING_PROVIDERS.keys())

        by_app: dict[str, AppPendingGroup] = {}
        degraded: list[str] = []
        total = 0

        for app_id in selected:
            provider = PENDING_PROVIDERS.get(app_id)
            if provider is None:
                # 未知应用 id：不是失败（不进 degraded），静默跳过。
                continue
            try:
                items = await provider(db, owner_hasn_id=owner_hasn_id, limit=limit)
            except Exception as exc:
                log.warning(f'[workbench.pending] provider {app_id} 读取失败，进 degraded：{exc}')
                degraded.append(app_id)
                continue
            if not items:
                continue
            by_app[app_id] = AppPendingGroup(app_id=app_id, count=len(items), items=items)
            total += len(items)

        return PendingScanResult(total=total, by_app=by_app, degraded=degraded)


workbench_pending_aggregator = WorkbenchPendingAggregator()
