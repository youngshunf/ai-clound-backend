"""项目管理 API 端点"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from backend.app.project.schema.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from backend.app.project.service.project_service import project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.post('', summary='创建项目', dependencies=[DependsJwtAuth])
async def create_project(
    db: CurrentSessionTransaction,
    request: Request,
    obj: ProjectCreate,
) -> ResponseSchemaModel[ProjectResponse]:
    """创建项目"""
    project = await project_service.create(db=db, obj=obj, user_id=request.user.id)
    return response_base.success(data=project)


@router.get('', summary='获取项目列表', dependencies=[DependsJwtAuth, DependsPagination])
async def get_projects(
    db: CurrentSession,
    request: Request,
    name: Annotated[str | None, Query(description='项目名称')] = None,
    industry: Annotated[str | None, Query(description='行业')] = None,
) -> ResponseSchemaModel[PageData[ProjectListResponse]]:
    """获取当前用户的项目列表"""
    page_data = await project_service.get_list(
        db=db,
        user_id=request.user.id,
        name=name,
        industry=industry,
    )
    return response_base.success(data=page_data)


@router.get('/default', summary='获取默认项目', dependencies=[DependsJwtAuth])
async def get_default_project(
    db: CurrentSession,
    request: Request,
) -> ResponseSchemaModel[ProjectResponse | None]:
    """获取当前用户的默认项目"""
    project = await project_service.get_default(db=db, user_id=request.user.id)
    return response_base.success(data=project)


@router.get('/{pk}', summary='获取项目详情', dependencies=[DependsJwtAuth])
async def get_project(
    db: CurrentSession,
    pk: Annotated[int, Path(description='项目 ID')],
) -> ResponseSchemaModel[ProjectResponse]:
    """获取项目详情"""
    project = await project_service.get(db=db, project_id=pk)
    return response_base.success(data=project)


@router.put('/{pk}', summary='更新项目', dependencies=[DependsJwtAuth])
async def update_project(
    db: CurrentSessionTransaction,
    request: Request,
    pk: Annotated[int, Path(description='项目 ID')],
    obj: ProjectUpdate,
) -> ResponseModel:
    """更新项目"""
    count = await project_service.update(db=db, project_id=pk, obj=obj, user_id=request.user.id)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.post('/{pk}/set-default', summary='设为默认项目', dependencies=[DependsJwtAuth])
async def set_default_project(
    db: CurrentSessionTransaction,
    request: Request,
    pk: Annotated[int, Path(description='项目 ID')],
) -> ResponseModel:
    """设为默认项目"""
    count = await project_service.set_default(db=db, project_id=pk, user_id=request.user.id)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete('/{pk}', summary='删除项目', dependencies=[DependsJwtAuth])
async def delete_project(
    db: CurrentSessionTransaction,
    request: Request,
    pk: Annotated[int, Path(description='项目 ID')],
) -> ResponseModel:
    """删除项目 (软删除)"""
    count = await project_service.delete(db=db, project_id=pk, user_id=request.user.id)
    if count > 0:
        return response_base.success()
    return response_base.fail()
