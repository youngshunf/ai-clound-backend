"""用户云存储逐 Owner 迁移管理端 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from backend.app.hasn.schema.owner_storage_admin_api import (
    CreateStorageMigrationParam,
    RollbackStorageMigrationParam,
)
from backend.app.hasn.service.hasn_audit_log_service import hasn_audit_log_service
from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, async_db_session

router = APIRouter()
_owner_storage = OwnerStorageService(async_db_session)
_MUTATE_GUARD = [
    Depends(RequestPermission('hasn:storage:migration')),
    DependsRBAC,
]


def _audit_actor(request: Request) -> str:
    """把后台登录用户映射为审计因果链作用域。"""
    return f'admin:{request.user.id}'


@router.post('', summary='创建逐 Owner 存储迁移', dependencies=_MUTATE_GUARD)
async def create_storage_migration(
    request: Request,
    obj: CreateStorageMigrationParam,
) -> ResponseSchemaModel[dict]:
    data = await _owner_storage.create_migration(
        owner_hasn_id=obj.owner_hasn_id,
        target_storage_by_access=obj.target_storage_by_access,
        observation_seconds=obj.observation_seconds,
        audit_actor_id=_audit_actor(request),
    )
    return response_base.success(data=data)


@router.get(
    '/{owner_hasn_id}/{job_id}',
    summary='读取逐 Owner 存储迁移状态',
    dependencies=_MUTATE_GUARD,
)
async def get_storage_migration(
    owner_hasn_id: Annotated[str, Path(min_length=1, max_length=40)],
    job_id: Annotated[str, Path(min_length=1, max_length=40)],
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await _owner_storage.migration_status(
            owner_hasn_id=owner_hasn_id,
            job_id=job_id,
        )
    )


@router.post(
    '/{owner_hasn_id}/{job_id}/pause',
    summary='暂停逐 Owner 存储迁移',
    dependencies=_MUTATE_GUARD,
)
async def pause_storage_migration(
    request: Request,
    owner_hasn_id: Annotated[str, Path(min_length=1, max_length=40)],
    job_id: Annotated[str, Path(min_length=1, max_length=40)],
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await _owner_storage.pause_migration(
            owner_hasn_id=owner_hasn_id,
            job_id=job_id,
            audit_actor_id=_audit_actor(request),
        )
    )


@router.post(
    '/{owner_hasn_id}/{job_id}/resume',
    summary='恢复逐 Owner 存储迁移',
    dependencies=_MUTATE_GUARD,
)
async def resume_storage_migration(
    request: Request,
    owner_hasn_id: Annotated[str, Path(min_length=1, max_length=40)],
    job_id: Annotated[str, Path(min_length=1, max_length=40)],
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await _owner_storage.resume_migration(
            owner_hasn_id=owner_hasn_id,
            job_id=job_id,
            audit_actor_id=_audit_actor(request),
        )
    )


@router.post(
    '/{owner_hasn_id}/{job_id}/rollback',
    summary='回滚逐 Owner 存储迁移',
    dependencies=_MUTATE_GUARD,
)
async def rollback_storage_migration(
    request: Request,
    db: CurrentSession,
    owner_hasn_id: Annotated[str, Path(min_length=1, max_length=40)],
    job_id: Annotated[str, Path(min_length=1, max_length=40)],
    obj: RollbackStorageMigrationParam,
) -> ResponseSchemaModel[dict]:
    data = await _owner_storage.rollback_migration(
        owner_hasn_id=owner_hasn_id,
        job_id=job_id,
        limit=obj.limit,
    )
    await hasn_audit_log_service.append(
        db=db,
        actor_id=_audit_actor(request),
        actor_type='human',
        action='storage_migration_rollback',
        target_type='storage_job',
        target_id=job_id,
        details={
            'job_id': job_id,
            'owner_hasn_id': owner_hasn_id,
            **data,
        },
        severity='warning',
    )
    await db.commit()
    return response_base.success(data=data)
