"""金融投研两容器（策略 / 影子账户）的平台项目挂靠 adapter 注册（doc38 §4 层2 / 05 §4）。

import 即把两个容器级 LinkageAdapter 注册进 project_linkage_registry：
- `finance/strategies` → Strategy.platform_project_id（长生命周期容器：建→回测→迭代跨数周）
- `finance/shadow` → ShadowAccount.platform_project_id（复盘容器：季季对比的连续体）

`domain` 必须与各自 ResourceDescriptor.uri_domain 完全一致；两者 id 均为整型主键
（id_is_uuid=False），is_container=True 参与项目总览并集读反查。项目=视角，非权限边界/挂载点/
容器接管（doc38 三铁律）。由 ai_native_app_registry 在 import 链上加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn_finance.model.backtest_report import BacktestReport
from backend.app.hasn_finance.model.shadow_account import ShadowAccount
from backend.app.hasn_finance.model.strategy import Strategy
from backend.app.hasn_finance.model.trade_review import TradeReview
from backend.app.hasn_project.service.project_linkage_registry import LinkageAdapter, project_linkage_registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _build_uri(resource_kind: str, server_id: object) -> str:
    """经 manifest descriptor 构造云端权威资源 URI，禁止在 adapter 手拼域。"""
    # 延迟导入，避免 ai_native_app_registry 加载本注册模块时形成循环导入。
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    descriptor = ai_native_app_registry.resource_descriptor('finance', resource_kind)
    if descriptor is None:
        raise RuntimeError(f'finance descriptor 缺失：{resource_kind}')
    return descriptor.build_uri(str(server_id))


async def _strategy_related_uris(db: AsyncSession, owner: str, rows: tuple[Any, ...]) -> list[str]:
    """取已挂靠策略名下的历史回测 URI。"""
    strategy_ids = [row.id for row in rows]
    ids = (
        await db.execute(
            sa.select(BacktestReport.id).where(
                BacktestReport.owner_id == owner,
                BacktestReport.strategy_id.in_(strategy_ids),
                BacktestReport.status != 'deleted',
            )
        )
    ).scalars().all()
    return [_build_uri('finance.backtest_report', server_id) for server_id in ids]


async def _strategy_related_uri_pairs(
    db: AsyncSession,
    owner: str,
    rows_by_project: dict[UUID, tuple[Any, ...]],
) -> list[tuple[UUID, str]]:
    """批量映射策略历史回测 URI，供项目列表 set-based 聚合复用。"""
    strategy_to_project = {
        row.id: project_id
        for project_id, rows in rows_by_project.items()
        for row in rows
    }
    if not strategy_to_project:
        return []
    rows = (
        await db.execute(
            sa.select(BacktestReport.id, BacktestReport.strategy_id).where(
                BacktestReport.owner_id == owner,
                BacktestReport.strategy_id.in_(strategy_to_project),
                BacktestReport.status != 'deleted',
            )
        )
    ).all()
    return [
        (strategy_to_project[strategy_id], _build_uri('finance.backtest_report', report_id))
        for report_id, strategy_id in rows
    ]


async def _shadow_related_uris(db: AsyncSession, owner: str, rows: tuple[Any, ...]) -> list[str]:
    """取已挂靠影子账户名下的历史复盘及其影子回测 URI。"""
    shadow_ids = [row.id for row in rows]
    review_rows = (
        await db.execute(
            sa.select(TradeReview.id, TradeReview.shadow_backtest_id).where(
                TradeReview.owner_id == owner,
                TradeReview.shadow_account_id.in_(shadow_ids),
                TradeReview.status != 'deleted',
            )
        )
    ).all()
    uris = [_build_uri('finance.trade_review', review_id) for review_id, _ in review_rows]
    uris.extend(
        _build_uri('finance.backtest_report', backtest_id)
        for _, backtest_id in review_rows
        if backtest_id is not None
    )
    return uris


async def _shadow_related_uri_pairs(
    db: AsyncSession,
    owner: str,
    rows_by_project: dict[UUID, tuple[Any, ...]],
) -> list[tuple[UUID, str]]:
    """批量映射影子账户名下复盘与回测 URI，避免项目列表逐项目反查。"""
    shadow_to_project = {
        row.id: project_id
        for project_id, rows in rows_by_project.items()
        for row in rows
    }
    if not shadow_to_project:
        return []
    rows = (
        await db.execute(
            sa.select(TradeReview.id, TradeReview.shadow_backtest_id, TradeReview.shadow_account_id).where(
                TradeReview.owner_id == owner,
                TradeReview.shadow_account_id.in_(shadow_to_project),
                TradeReview.status != 'deleted',
            )
        )
    ).all()
    pairs: list[tuple[UUID, str]] = []
    for review_id, backtest_id, shadow_account_id in rows:
        project_id = shadow_to_project[shadow_account_id]
        pairs.append((project_id, _build_uri('finance.trade_review', review_id)))
        if backtest_id is not None:
            pairs.append((project_id, _build_uri('finance.backtest_report', backtest_id)))
    return pairs


# 策略容器：长生命周期，项目总览要能看到「沉淀了哪些策略」（doc38 §4 层2 表）
project_linkage_registry.register(
    LinkageAdapter(
        domain='finance/strategies',
        model=Strategy,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='finance',
        kind='strategy',
        title_column='name',
        revision_column='revision',
        sync_kind='finance',
        related_resource_uris=_strategy_related_uris,
        related_resource_uri_pairs=_strategy_related_uri_pairs,
    )
)

# 影子账户容器：复盘的季季连续体，可整体挂进改进类项目（doc38 §4 层2 表）
project_linkage_registry.register(
    LinkageAdapter(
        domain='finance/shadow',
        model=ShadowAccount,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
        app_id='finance',
        kind='shadow_account',
        title_column='account_alias',
        revision_column='revision',
        sync_kind='finance',
        related_resource_uris=_shadow_related_uris,
        related_resource_uri_pairs=_shadow_related_uri_pairs,
    )
)
