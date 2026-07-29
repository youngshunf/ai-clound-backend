"""Growth Owner API 的平台项目上下文与幂等启用服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_provision import (
    GrowthProjectProvision,
)
from backend.app.hasn_growth.schema.growth_project import GetGrowthProjectDetail
from backend.app.hasn_growth.service.growth_profile_service import (
    growth_profile_service,
)
from backend.app.hasn_growth.service.review_service import growth_review_service
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
        'goal': row.goal,
        'status': row.status,
        'enterprise_id': str(row.enterprise_id) if row.enterprise_id is not None else None,
    }


_PROVISION_STEPS = (
    'create_funnel',
    'create_knowledge',
    'attach_knowledge',
    'seed_knowledge',
)


def _serialize_provision(row: GrowthProjectProvision) -> dict[str, Any]:
    """只暴露 UI 恢复需要的可靠步骤事实，不返回客户端幂等键。"""
    return {
        'step': row.step,
        'status': row.status,
        'attempts': row.attempts,
        'next_retry_time': (row.next_retry_time.isoformat() if row.next_retry_time is not None else None),
        'last_error': row.last_error,
        'started_time': (row.started_time.isoformat() if row.started_time is not None else None),
        'finished_time': (row.finished_time.isoformat() if row.finished_time is not None else None),
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

    @staticmethod
    async def _provision_rows(
        db: AsyncSession,
        *,
        growth_project_id: UUID,
    ) -> list[GrowthProjectProvision]:
        rows = (
            await db.execute(
                sa
                .select(GrowthProjectProvision)
                .where(GrowthProjectProvision.growth_project_id == growth_project_id)
                .order_by(GrowthProjectProvision.id)
            )
        ).scalars()
        return list(rows)

    async def _serialize_with_provision(
        self,
        db: AsyncSession,
        row: GrowthProject,
    ) -> dict[str, Any]:
        data = _serialize_growth(row)
        provisions = await self._provision_rows(
            db,
            growth_project_id=row.id,
        )
        data['provision_steps'] = [_serialize_provision(provision) for provision in provisions]
        data['readiness'] = await growth_profile_service.compute_readiness(
            db,
            owner_hasn_id=row.owner_hasn_id,
            growth_project_id=row.id,
        )
        return data

    @staticmethod
    async def _owned_growth(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        for_update: bool,
    ) -> GrowthProject:
        statement = sa.select(GrowthProject).where(
            GrowthProject.id == _parse_uuid(growth_project_id),
            GrowthProject.owner_hasn_id == owner_hasn_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await db.execute(statement)).scalar_one_or_none()
        if row is None:
            raise _not_found()
        return row

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
                await self._serialize_with_provision(db, growth_project) if growth_project is not None else None
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
        growth_project = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        return await self._serialize_with_provision(db, growth_project)

    async def enable(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        owner_user_id: int,
        platform_project_id: str | UUID,
        name: str | None,
        tagline: str | None,
        command_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """锁定平台项目后幂等启用，串行化同项目并发创建。"""
        try:
            canonical_command_id = str(UUID(command_id))
        except (TypeError, ValueError) as exc:
            raise errors.RequestError(
                msg='trace_id 必须是有效 UUID',
                data={'error_code': 'INVALID_TRACE_ID'},
            ) from exc
        normalized_idempotency_key = idempotency_key.strip()
        if not normalized_idempotency_key:
            raise errors.RequestError(
                msg='idempotency_key 不能为空',
                data={'error_code': 'IDEMPOTENCY_KEY_REQUIRED'},
            )
        if len(normalized_idempotency_key) > 200:
            raise errors.RequestError(
                msg='idempotency_key 最长 200 个字符',
                data={'error_code': 'IDEMPOTENCY_KEY_TOO_LONG'},
            )
        # 外部资源创建前先按两个稳定键串行化；不同平台误复用同一键也必须返回确定 409，
        # 不能等数据库唯一约束抛成 500。
        for lock_key in sorted((
            f'growth:command:{canonical_command_id}',
            f'growth:idempotency:{normalized_idempotency_key}',
        )):
            await db.execute(
                sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': lock_key},
            )
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

        idempotency_row = (
            await db.execute(
                sa.select(GrowthProjectProvision).where(
                    GrowthProjectProvision.idempotency_key == normalized_idempotency_key,
                    GrowthProjectProvision.step == 'create_funnel',
                )
            )
        ).scalar_one_or_none()
        command_row = (
            await db.execute(
                sa.select(GrowthProjectProvision).where(
                    GrowthProjectProvision.command_id == canonical_command_id,
                    GrowthProjectProvision.step == 'create_funnel',
                )
            )
        ).scalar_one_or_none()
        key_presence_mismatch = (idempotency_row is None) != (command_row is None)
        key_target_mismatch = (
            idempotency_row is not None
            and command_row is not None
            and idempotency_row.growth_project_id != command_row.growth_project_id
        )
        if key_presence_mismatch or key_target_mismatch:
            raise errors.ConflictError(
                msg='idempotency_key 与 trace_id 不属于同一次获客启用',
                data={'error_code': 'GROWTH_IDEMPOTENCY_CONFLICT'},
            )
        keyed_growth_id = (
            idempotency_row.growth_project_id
            if idempotency_row is not None
            else (command_row.growth_project_id if command_row is not None else None)
        )
        if keyed_growth_id is not None:
            keyed_growth = await db.get(GrowthProject, keyed_growth_id)
            if keyed_growth is None or keyed_growth.platform_project_id != platform_project.id:
                raise errors.ConflictError(
                    msg='trace_id 或 idempotency_key 已用于其他获客项目',
                    data={'error_code': 'GROWTH_IDEMPOTENCY_CONFLICT'},
                )

        existing = (
            await db.execute(sa.select(GrowthProject).where(GrowthProject.platform_project_id == platform_project.id))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.owner_hasn_id != owner_hasn_id:
                raise _not_found()
            if existing.name != normalized_name or existing.tagline != normalized_tagline:
                raise errors.ConflictError(
                    msg='该平台项目已启用另一个获客漏斗',
                    data={'error_code': 'GROWTH_PROJECT_ALREADY_EXISTS'},
                )
            return {
                'created': False,
                'growth_project': await self._serialize_with_provision(
                    db,
                    existing,
                ),
            }

        growth_project = GrowthProject(
            platform_project_id=platform_project.id,
            user_id=owner_user_id,
            owner_hasn_id=owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            name=normalized_name,
            tagline=normalized_tagline,
            owner_agent_id=platform_project.bound_agent_id,
            status='draft',
            provision_status='pending',
        )
        db.add(growth_project)
        await db.flush()
        now = datetime.now(UTC)
        for index, step in enumerate(_PROVISION_STEPS):
            db.add(
                GrowthProjectProvision(
                    growth_project_id=growth_project.id,
                    command_id=canonical_command_id,
                    idempotency_key=normalized_idempotency_key,
                    step=step,
                    status='success' if index == 0 else 'pending',
                    attempts=1 if index == 0 else 0,
                    started_time=now if index == 0 else None,
                    finished_time=now if index == 0 else None,
                )
            )
        await db.flush()
        return {
            'created': True,
            'growth_project': await self._serialize_with_provision(
                db,
                growth_project,
            ),
        }

    async def pause(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """暂停自动动作；重复暂停幂等，归档态必须先显式恢复。"""
        growth_project = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        if growth_project.status == 'archived':
            raise errors.ConflictError(
                msg='获客项目已归档，请先恢复项目',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        growth_project.status = 'paused'
        await growth_review_service.suspend_project_tasks(
            db,
            growth_project=growth_project,
        )
        await db.flush()
        return await self._serialize_with_provision(db, growth_project)

    async def update(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        name: str | None,
        tagline: str | None,
    ) -> dict[str, Any]:
        """更新项目门面信息；归档态保持只读。"""
        growth_project = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        if growth_project.status == 'archived':
            raise errors.ConflictError(
                msg='获客项目已归档，不能修改',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise errors.RequestError(msg='name 不能为空')
            growth_project.name = normalized_name
        if tagline is not None:
            growth_project.tagline = tagline.strip() or None
        await db.flush()
        return await self._serialize_with_provision(db, growth_project)

    async def archive(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """把 Growth 切为只读归档态；不删除已创建资源。"""
        growth_project = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        growth_project.status = 'archived'
        await growth_review_service.suspend_project_tasks(
            db,
            growth_project=growth_project,
        )
        await db.flush()
        return await self._serialize_with_provision(db, growth_project)

    async def restore(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """归档恢复只进入 paused；不会隐式启动发送、任务或 provisioning。"""
        growth_project = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        platform_project = await self._owned_platform_project(
            db,
            owner_hasn_id=owner_hasn_id,
            platform_project_id=growth_project.platform_project_id,
            for_update=False,
        )
        if platform_project.status != 'active':
            raise errors.ConflictError(
                msg='平台项目仍处于归档状态，请先恢复平台项目',
                data={'error_code': 'PLATFORM_PROJECT_ARCHIVED'},
            )
        if growth_project.status != 'archived':
            raise errors.ConflictError(
                msg='只有已归档的获客项目可以恢复',
                data={'error_code': 'GROWTH_PROJECT_NOT_ARCHIVED'},
            )
        growth_project.status = 'paused'
        await db.flush()
        return await self._serialize_with_provision(db, growth_project)

    async def resume(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """主人显式恢复自动动作；只有 provisioning ready 才能进入 active。"""
        growth_project = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        platform_project = await self._owned_platform_project(
            db,
            owner_hasn_id=owner_hasn_id,
            platform_project_id=growth_project.platform_project_id,
            for_update=False,
        )
        if platform_project.status != 'active':
            raise errors.ConflictError(
                msg='平台项目已归档，不能恢复获客自动动作',
                data={'error_code': 'PLATFORM_PROJECT_ARCHIVED'},
            )
        if growth_project.status == 'archived':
            raise errors.ConflictError(
                msg='请先恢复已归档的获客项目',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        if growth_project.provision_status != 'ready':
            raise errors.ConflictError(
                msg='获客项目尚未完成基础资源开通',
                data={'error_code': 'GROWTH_PROJECT_NOT_READY'},
            )
        readiness = await growth_profile_service.compute_readiness(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project.id,
        )
        if not readiness['ready']:
            raise errors.ConflictError(
                msg='获客画像或知识库尚未就绪，不能恢复自动动作',
                data={
                    'error_code': 'GROWTH_PROJECT_READINESS_BLOCKED',
                    'blocking_reasons': readiness['blocking_reasons'],
                },
            )
        growth_project.status = 'active'
        await db.flush()
        return await self._serialize_with_provision(db, growth_project)


growth_project_app_service = GrowthProjectAppService()
