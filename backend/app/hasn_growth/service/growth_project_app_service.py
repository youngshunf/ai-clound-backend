"""Growth Owner API 的平台项目上下文与幂等启用服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.schema.growth_project import GetGrowthProjectDetail
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _not_found() -> errors.NotFoundError:
    return errors.NotFoundError(msg='平台项目或获客漏斗不存在')


def _parse_uuid(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise _not_found() from exc


def _serialize_growth(row: GrowthProject) -> dict[str, Any]:
    return GetGrowthProjectDetail.model_validate(row).model_dump(mode='json')


def _serialize_platform_project(row: HasnProject) -> dict[str, Any]:
    return {
        'id': str(row.id),
        'name': row.name,
        'status': row.status,
        'enterprise_id': str(row.enterprise_id) if row.enterprise_id is not None else None,
    }


class GrowthProjectAppService:
    """Owner 隔离的 Growth 项目读写服务。"""

    @staticmethod
    async def _owned_platform_project(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        platform_project_id: str | UUID,
        for_update: bool,
    ) -> HasnProject:
        statement = sa.select(HasnProject).where(
            HasnProject.id == _parse_uuid(platform_project_id),
            HasnProject.owner_id == owner_hasn_id,
        )
        if for_update:
            statement = statement.with_for_update()
        project = (await db.execute(statement)).scalar_one_or_none()
        if project is None:
            raise _not_found()
        return project

    @staticmethod
    def _assert_personal_project(project: HasnProject) -> None:
        """企业 UUID 与 Growth bigint 尚无权威映射，完成平台门禁前拒绝企业模式。"""
        if project.enterprise_id is not None:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg='企业身份映射尚未完成，暂不能启用获客漏斗',
                data={'error_code': 'ENTERPRISE_IDENTITY_MAPPING_REQUIRED'},
            )

    async def get_for_platform(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        platform_project_id: str | UUID,
    ) -> dict[str, Any]:
        """读取平台项目及其唯一 Growth；未启用是合法的 null 状态。"""
        platform_project = await self._owned_platform_project(
            db,
            owner_hasn_id=owner_hasn_id,
            platform_project_id=platform_project_id,
            for_update=False,
        )
        self._assert_personal_project(platform_project)
        growth_project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.platform_project_id == platform_project.id,
                    GrowthProject.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        return {
            'platform_project': _serialize_platform_project(platform_project),
            'growth_project': (
                _serialize_growth(growth_project)
                if growth_project is not None
                else None
            ),
        }

    async def get_by_id(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """按云端 Growth UUID 读取；不存在和跨 Owner 都返回 404。"""
        growth_project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.id == _parse_uuid(growth_project_id),
                    GrowthProject.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if growth_project is None:
            raise _not_found()
        return _serialize_growth(growth_project)

    async def enable(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        owner_user_id: int,
        platform_project_id: str | UUID,
        name: str | None,
        tagline: str | None,
    ) -> dict[str, Any]:
        """锁定平台项目后幂等启用，串行化同项目并发创建。"""
        platform_project = await self._owned_platform_project(
            db,
            owner_hasn_id=owner_hasn_id,
            platform_project_id=platform_project_id,
            for_update=True,
        )
        if platform_project.status != 'active':
            raise errors.ConflictError(
                msg='项目已归档，不能启用获客漏斗',
                data={'error_code': 'PROJECT_ARCHIVED'},
            )
        self._assert_personal_project(platform_project)
        normalized_name = (name or '').strip() or platform_project.name
        normalized_tagline = (tagline or '').strip() or None

        existing = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.platform_project_id == platform_project.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.owner_hasn_id != owner_hasn_id:
                raise _not_found()
            if (
                existing.name != normalized_name
                or existing.tagline != normalized_tagline
            ):
                raise errors.ConflictError(
                    msg='该平台项目已启用另一个获客漏斗',
                    data={'error_code': 'GROWTH_PROJECT_ALREADY_EXISTS'},
                )
            return {
                'created': False,
                'growth_project': _serialize_growth(existing),
            }

        growth_project = GrowthProject(
            platform_project_id=platform_project.id,
            user_id=owner_user_id,
            owner_hasn_id=owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            name=normalized_name,
            tagline=normalized_tagline,
            status='draft',
            provision_status='pending',
        )
        db.add(growth_project)
        await db.flush()
        return {
            'created': True,
            'growth_project': _serialize_growth(growth_project),
        }


growth_project_app_service = GrowthProjectAppService()
