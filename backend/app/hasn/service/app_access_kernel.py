"""应用准入合并 kernel（doc18 G3 应用权益门共享内核 · 实施/103 U3）。

「某 app 对某 owner（在其当前空间）是否准入」此前有两个消费面各写一遍：
AI-Native 运行时网关 `_entitlement_denial`（工具调用付费墙）与工作台 `list_apps`
（应用中心可见性）。本模块把「app access → 合并准入 dict」抽成单一纯逻辑，令两面
（外加 G3 权益门）口径永不分叉（doc04 §6.6 M1 `merge_access`「顺序即优先级 + 自解优先」）。

设计事实源：docs/hasn-node设计文档/MCP统一工具体系/实施/103 §4；doc04 §5.2/§6.6。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.service import app_catalog_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# not-in-catalog 的 app_id（底座工具映射不到 catalog 行）视为放行——G3 只拦「付费应用未准入」，
# 不存在的应用 / 免费应用一律不拦。reason 仅作调试标注，allowed=True 时消费面不读。
_NOT_IN_CATALOG = {'allowed': True, 'reason': 'not_in_catalog'}


async def resolve_active_enterprise_id(db: AsyncSession, owner_hasn_id: str) -> int | None:
    """解析主人当前激活的企业空间 id（doc12/02「分身无独立企业身份，企业上下文=主人活跃工作区」）。

    权威在 `hasn_owner_workbench_pref.active_enterprise_id`（瘦指针）。个人空间 / 无偏好行 → None。
    """
    from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref

    return (
        (
            await db.execute(
                sa.select(HasnOwnerWorkbenchPref.active_enterprise_id).where(
                    HasnOwnerWorkbenchPref.owner_hasn_id == owner_hasn_id
                )
            )
        )
        .scalars()
        .first()
    )


async def resolve_merged_app_access(
    db: AsyncSession,
    *,
    app_id: str,
    owner_hasn_id: str,
    active_enterprise_id: int | None,
) -> dict:
    """解析某 app 对某 owner（在其当前空间）的合并准入 dict（doc04 M1）。

    - catalog 无此 app → 视为放行（`_NOT_IN_CATALOG`，底座工具走这里）；
    - owner 维度用 `resolve_app_access`（tier/purchase 实时判定）；
    - 主人处于企业空间且应用支持企业形态 → 叠加企业维度（含命名席位，member=该主人）；
    - 纯企业应用在个人空间 → owner 维度收窄为 `need_enterprise_space`（引导切换，不显付费墙）；
    - 最终 `merge_access(owner, enterprise)`：`allowed = owner OR enterprise`，reason 按 M1 自解优先。

    与工作台 `list_apps`（lines 808-845）+ 网关 `_entitlement_denial` 口径逐位一致。
    """
    cat = await app_catalog_service.get_catalog(db, app_id=app_id)
    if cat is None:
        return dict(_NOT_IN_CATALOG)

    owner_access = await app_catalog_service.resolve_app_access(db, catalog=cat, owner_hasn_id=owner_hasn_id)
    scope = set(cat.scope or [])
    enterprise_access = None
    if active_enterprise_id is not None and 'enterprise' in scope:
        enterprise_access = await app_catalog_service.resolve_app_access(
            db,
            catalog=cat,
            owner_hasn_id=owner_hasn_id,
            subject_type='enterprise',
            subject_id=str(active_enterprise_id),
            member_hasn_id=owner_hasn_id,
        )
    elif scope == {'enterprise'} and active_enterprise_id is None:
        owner_access = {
            'allowed': False,
            'reason': 'need_enterprise_space',
            'requires': 'enterprise_space',
            'min_tier': cat.min_tier,
            'price': None,
            'trial_available': False,
            'entitlement_expires_at': None,
        }
    return app_catalog_service.merge_access(owner_access, enterprise_access)


def app_access_denial_reason(access: dict) -> str | None:
    """从合并准入 dict 提取拒绝 reason；准入（allowed=True）→ None。"""
    if access.get('allowed'):
        return None
    return access.get('reason') or 'need_purchase'


async def prefetch_app_access_map(
    db: AsyncSession,
    *,
    owner_hasn_id: str,
    active_enterprise_id: int | None,
    app_ids: frozenset[str],
) -> dict[str, dict]:
    """per-request 批量预取「本 owner/空间」对一组 app 的准入 map（103 §4 性能条）。

    tool.search 逐工具求值时，evaluate 纯查此 map（零 IO）。app_ids 是有限小集合
    （G3 挂门的 catalog app_id，见 tool_app_registry.GATED_APP_IDS），多为 free → 单查即短路。
    """
    result: dict[str, dict] = {}
    for app_id in app_ids:
        result[app_id] = await resolve_merged_app_access(
            db, app_id=app_id, owner_hasn_id=owner_hasn_id, active_enterprise_id=active_enterprise_id
        )
    return result
