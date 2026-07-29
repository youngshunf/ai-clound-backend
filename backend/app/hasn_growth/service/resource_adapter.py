"""获客四类资源的统一实例 ACL 元信息适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn.service.authz.resource_registry import (
    ResourceKindAdapter,
    ResourceMeta,
    resource_kind_registry,
)
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.opportunity import Opportunity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _to_uuid(resource_id: str) -> UUID | None:
    try:
        return UUID(resource_id)
    except (TypeError, ValueError):
        return None


def _to_int(resource_id: str) -> int | None:
    try:
        return int(resource_id)
    except (TypeError, ValueError):
        return None


def _meta(row: Any, project: GrowthProject) -> ResourceMeta:
    """由漏斗归属生成 ACL 元信息，迁移期子表的冗余字段不作为权威。"""
    return ResourceMeta(
        resource_id=str(row.id),
        owner_hasn_id=project.owner_hasn_id,
        owner_scope=project.owner_scope,
        enterprise_id=project.enterprise_id,
        visibility='enterprise' if project.owner_scope == 'enterprise' else 'private',
        row=row,
    )


class GrowthProjectResourceAdapter:
    """获客漏斗资源；只接受云端 UUID 主键。"""

    resource_type = 'growth_project'
    id_param_aliases: tuple[str, ...] = ('growth_project_id',)

    async def load_meta(
        self,
        db: AsyncSession,
        resource_id: str,
    ) -> ResourceMeta | None:
        project_id = _to_uuid(resource_id)
        if project_id is None:
            return None
        project = (
            await db.execute(
                sa.select(GrowthProject).where(GrowthProject.id == project_id)
            )
        ).scalar_one_or_none()
        return None if project is None else _meta(project, project)


class GrowthLeadsResourceAdapter(GrowthProjectResourceAdapter):
    """漏斗线索池与漏斗共享同一个云端 UUID 和归属。"""

    resource_type = 'growth_leads'


class GrowthCustomerResourceAdapter:
    """获客客户资源；归属只从其云端漏斗解析。"""

    resource_type = 'growth_customer'
    id_param_aliases: tuple[str, ...] = ('customer_id',)

    async def load_meta(
        self,
        db: AsyncSession,
        resource_id: str,
    ) -> ResourceMeta | None:
        customer_id = _to_int(resource_id)
        if customer_id is None:
            return None
        result = (
            await db.execute(
                sa.select(Customer, GrowthProject)
                .join(
                    GrowthProject,
                    Customer.growth_project_id == GrowthProject.id,
                )
                .where(Customer.id == customer_id)
            )
        ).one_or_none()
        if result is None:
            return None
        customer, project = result
        return _meta(customer, project)


class GrowthOpportunityResourceAdapter:
    """获客商机资源；归属只从其云端漏斗解析。"""

    resource_type = 'growth_opportunity'
    id_param_aliases: tuple[str, ...] = ('opportunity_id',)

    async def load_meta(
        self,
        db: AsyncSession,
        resource_id: str,
    ) -> ResourceMeta | None:
        opportunity_id = _to_int(resource_id)
        if opportunity_id is None:
            return None
        result = (
            await db.execute(
                sa.select(Opportunity, GrowthProject)
                .join(
                    GrowthProject,
                    Opportunity.growth_project_id == GrowthProject.id,
                )
                .where(Opportunity.id == opportunity_id)
            )
        ).one_or_none()
        if result is None:
            return None
        opportunity, project = result
        return _meta(opportunity, project)


def register() -> None:
    """注册四类 Growth 资源，重复 import 保持幂等。"""
    def register_one(adapter: ResourceKindAdapter) -> None:
        if adapter.resource_type not in resource_kind_registry.registered_types():
            resource_kind_registry.register(adapter)

    register_one(GrowthProjectResourceAdapter())
    register_one(GrowthLeadsResourceAdapter())
    register_one(GrowthCustomerResourceAdapter())
    register_one(GrowthOpportunityResourceAdapter())


register()
