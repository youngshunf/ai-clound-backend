"""获客漏斗的平台项目挂靠声明与项目产物流聚合。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_linkage_registry import (
    LinkageAdapter,
    project_linkage_registry,
)
from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _build_uri(resource_kind: str, server_id: object) -> str:
    """经 manifest descriptor 构造云端权威资源 URI。"""
    from backend.app.hasn_core.app_platform import ai_native_app_registry

    descriptor = ai_native_app_registry.resource_descriptor('growth', resource_kind)
    if descriptor is None:
        raise RuntimeError(f'growth descriptor 缺失：{resource_kind}')
    return descriptor.build_uri(str(server_id))


async def _validate_growth_link(
    db: AsyncSession,
    owner: str,
    row: GrowthProject,
    platform_project_id: UUID,
) -> None:
    """挂靠前复核 Owner、active 与个人模式门禁，避免通用 link 绕过 Growth 启用约束。"""
    platform_project = (
        await db.execute(
            sa.select(HasnProject).where(
                HasnProject.id == platform_project_id,
                HasnProject.owner_id == owner,
            )
        )
    ).scalar_one_or_none()
    if platform_project is None:
        raise errors.NotFoundError(msg='目标平台项目不存在或不属于你')
    if platform_project.status != 'active':
        raise errors.ConflictError(
            msg='项目已归档，不能挂靠获客漏斗',
            data={'error_code': 'PROJECT_ARCHIVED'},
        )
    if (
        platform_project.enterprise_id is not None
        or row.owner_scope != 'personal'
        or row.enterprise_id is not None
    ):
        raise errors.RequestError(
            code=StandardResponseCode.HTTP_422,
            msg='企业身份映射尚未完成，暂不能挂靠获客漏斗',
            data={'error_code': 'ENTERPRISE_IDENTITY_MAPPING_REQUIRED'},
        )


async def _growth_related_uris(
    db: AsyncSession,
    owner: str,
    rows: tuple[Any, ...],
) -> list[str]:
    """列漏斗线索池、客户与商机 URI；owner 已由注册表定位漏斗时校验。"""
    del owner
    project_ids = [row.id for row in rows]
    if not project_ids:
        return []
    customer_ids = (
        await db.execute(
            sa.select(Customer.id).where(Customer.growth_project_id.in_(project_ids))
        )
    ).scalars().all()
    opportunity_ids = (
        await db.execute(
            sa.select(Opportunity.id).where(Opportunity.growth_project_id.in_(project_ids))
        )
    ).scalars().all()
    uris = [_build_uri('growth.leads', project_id) for project_id in project_ids]
    uris.extend(_build_uri('growth.customer', customer_id) for customer_id in customer_ids)
    uris.extend(
        _build_uri('growth.opportunity', opportunity_id)
        for opportunity_id in opportunity_ids
    )
    return uris


async def _growth_related_uri_pairs(
    db: AsyncSession,
    owner: str,
    rows_by_project: dict[UUID, tuple[Any, ...]],
) -> list[tuple[UUID, str]]:
    """批量派生 Growth 子资源到平台项目的稳定 URI 映射。"""
    del owner
    growth_to_platform = {
        row.id: project_id
        for project_id, rows in rows_by_project.items()
        for row in rows
    }
    if not growth_to_platform:
        return []

    pairs = [
        (platform_project_id, _build_uri('growth.leads', growth_project_id))
        for growth_project_id, platform_project_id in growth_to_platform.items()
    ]
    customer_rows = (
        await db.execute(
            sa.select(Customer.id, Customer.growth_project_id).where(
                Customer.growth_project_id.in_(growth_to_platform)
            )
        )
    ).all()
    pairs.extend(
        (
            growth_to_platform[growth_project_id],
            _build_uri('growth.customer', customer_id),
        )
        for customer_id, growth_project_id in customer_rows
    )
    opportunity_rows = (
        await db.execute(
            sa.select(Opportunity.id, Opportunity.growth_project_id).where(
                Opportunity.growth_project_id.in_(growth_to_platform)
            )
        )
    ).all()
    pairs.extend(
        (
            growth_to_platform[growth_project_id],
            _build_uri('growth.opportunity', opportunity_id),
        )
        for opportunity_id, growth_project_id in opportunity_rows
    )
    return pairs


project_linkage_registry.register(
    LinkageAdapter(
        domain='growth/projects',
        model=GrowthProject,
        id_column='id',
        owner_column='owner_hasn_id',
        attach_column='platform_project_id',
        id_is_uuid=True,
        is_container=True,
        app_id='growth',
        kind='growth_project',
        title_column='name',
        sync_kind='growth',
        allow_unlink=False,
        allow_relink=False,
        validate_link=_validate_growth_link,
        related_resource_uris=_growth_related_uris,
        related_resource_uri_pairs=_growth_related_uri_pairs,
    )
)
