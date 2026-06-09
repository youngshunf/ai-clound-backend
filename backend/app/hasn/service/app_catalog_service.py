"""应用目录 / 权益领域服务（C1 数据层）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md

职责：
- ``ensure_catalog_seeded``：从 ``workbench_app_registry`` 幂等播种 ``hasn_app_catalog``（迁移 M2）。
  **只插入缺失行，绝不回写已存在行的 display/价格**——这是「代码不覆盖运营改动」的关键
  （区别于 manifest 的 hash 自愈逻辑，见设计 §6.1）。
- ``sweep_expired_entitlements``：把 ``expires_at < now`` 的 active 权益置 expired（设计 §5.4 定时兜底）。

生成的 ``hasn_app_catalog_service`` / ``hasn_app_entitlement_service`` 负责 Admin CRUD；
本模块只承载播种与兜底这类领域逻辑，避免改动 codegen 产物。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.service.workbench_app_registry import WorkbenchApp, workbench_app_registry
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 工作台排序（小在前）。未列出的 app 落到默认值之后。
_CATALOG_SORT_ORDER: dict[str, int] = {
    'knowledge': 10,
    'community': 20,
    'presentation': 30,
}
_DEFAULT_SORT_ORDER = 100


def _catalog_row_from_workbench_app(app: WorkbenchApp) -> dict:
    """把 WorkbenchApp 映射为 catalog 行的默认值（迁移期单一来源）。

    新增字段（source/status/商业化…）取保守默认：全部内置、已上架、免费。
    """
    return {
        'app_id': app.id,
        'name': app.name,
        'icon': app.icon,
        'icon_asset_uri': None,
        'description': app.description,
        'source': 'builtin',
        'status': 'published',
        'execution_mode': app.execution_mode,
        'scope': list(app.scope),
        'collaboration_mode': app.collaboration_mode,
        'entry_route': app.entry_route,
        'sort_order': _CATALOG_SORT_ORDER.get(app.id, _DEFAULT_SORT_ORDER),
        'default_mount': app.install_policy == 'auto',
        'requires_role': app.requires_role,
        # 商业化默认：保持现状全免费（迁移 M2 不变量）。
        'access_type': 'free',
        'min_tier': None,
        'price_amount': None,
        'price_unit': 'cny',
        'billing_cycle': 'once',
        'trial_days': 0,
        'sku_ref': None,
        # 这三个 builtin 都有对应 code manifest。
        'manifest_present': True,
    }


async def ensure_catalog_seeded(db: AsyncSession) -> int:
    """幂等播种 catalog：仅插入缺失的 app_id 行，已存在行原样保留。

    返回新插入的行数。可在部署 reconcile / 测试夹具中调用。
    """
    existing = set((await db.execute(sa.select(HasnAppCatalog.app_id))).scalars().all())
    inserted = 0
    for app in workbench_app_registry.list():
        if app.id in existing:
            continue
        db.add(HasnAppCatalog(**_catalog_row_from_workbench_app(app)))
        inserted += 1
    if inserted:
        await db.flush()
    return inserted


async def sweep_expired_entitlements(db: AsyncSession) -> int:
    """把已过期的 active 权益置为 expired（定时兜底，与订阅过期兜底同构）。

    返回受影响行数。``expires_at IS NULL`` 视为永久买断，不受影响。
    """
    now = timezone.now()
    result = await db.execute(
        sa.update(HasnAppEntitlement)
        .where(
            HasnAppEntitlement.status == 'active',
            HasnAppEntitlement.expires_at.is_not(None),
            HasnAppEntitlement.expires_at < now,
        )
        .values(status='expired', updated_time=now)
    )
    return result.rowcount or 0
