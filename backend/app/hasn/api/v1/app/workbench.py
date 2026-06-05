from __future__ import annotations

import sqlalchemy as sa

from fastapi import APIRouter, Request

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_builtin_task_catalog import BuiltinTaskCatalogResponse
from backend.app.hasn.schema.hasn_owner_workbench_pref import PutWorkbenchPrefParam, WorkbenchPrefResponse
from backend.app.hasn.service.workbench_builtin_task_service import workbench_builtin_task_service
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.app.hasn.service.workbench_pref_service import workbench_pref_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction  # noqa: TC001

router = APIRouter()


async def _resolve_owner_id(request: Request, db: CurrentSession) -> str:
    """当前登录用户 → hasn_humans.hasn_id（owner 身份）。"""
    owner = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == request.user.id).limit(1))
    ).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return owner


@router.get('/workbench/pref', dependencies=[DependsJwtAuth], summary='读工作台偏好（主脑 + 简报设置）')
async def get_workbench_pref(request: Request, db: CurrentSession) -> ResponseSchemaModel[WorkbenchPrefResponse]:
    owner_id = await _resolve_owner_id(request, db)
    data = await workbench_pref_service.get_or_init_pref(db, owner_id)
    return response_base.success(data=data)


@router.put('/workbench/pref', dependencies=[DependsJwtAuth], summary='改工作台偏好（主脑 / 简报开关·时刻·数据源）')
async def update_workbench_pref(
    request: Request, db: CurrentSessionTransaction, obj: PutWorkbenchPrefParam
) -> ResponseSchemaModel[WorkbenchPrefResponse]:
    owner_id = await _resolve_owner_id(request, db)
    data = await workbench_pref_service.update_pref(
        db,
        owner_id,
        primary_agent_id=obj.primary_agent_id,
        briefing_enabled=obj.briefing_enabled,
        briefing_time=obj.briefing_time,
        briefing_sources=obj.briefing_sources,
    )
    return response_base.success(data=data)


@router.get('/workbench/builtin-tasks', dependencies=[DependsJwtAuth], summary='读官方内置任务目录（daemon 拉取播种）')
async def list_builtin_tasks(db: CurrentSession) -> ResponseSchemaModel[BuiltinTaskCatalogResponse]:
    data = await workbench_builtin_task_service.list_enabled(db)
    return response_base.success(data=data)


@router.get('/workbench/workspaces/current/apps', dependencies=[DependsJwtAuth], summary='当前工作空间已挂载应用')
async def current_workspace_apps(request: Request, db: CurrentSessionTransaction) -> ResponseModel:
    apps = await workbench_domain_service.list_current_workspace_apps(db, user_id=request.user.id)
    return response_base.success(data=apps)


@router.get('/workbench/apps', dependencies=[DependsJwtAuth], summary='工作台应用市场')
async def list_workbench_apps(request: Request, db: CurrentSession, workspace_kind: str | None = None) -> ResponseModel:
    apps = await workbench_domain_service.list_workbench_apps(
        db,
        user_id=request.user.id,
        workspace_kind=workspace_kind,
    )
    return response_base.success(data=apps)


@router.post('/workbench/workspaces/current/apps/{app_id}', dependencies=[DependsJwtAuth], summary='挂载应用')
async def enable_workbench_app(request: Request, db: CurrentSessionTransaction, app_id: str) -> ResponseModel:
    data = await workbench_domain_service.enable_current_workspace_app(db, user_id=request.user.id, app_id=app_id)
    return response_base.success(data=data)


@router.delete('/workbench/workspaces/current/apps/{app_id}', dependencies=[DependsJwtAuth], summary='卸载应用')
async def disable_workbench_app(request: Request, db: CurrentSessionTransaction, app_id: str) -> ResponseModel:
    data = await workbench_domain_service.disable_current_workspace_app(db, user_id=request.user.id, app_id=app_id)
    return response_base.success(data=data)
