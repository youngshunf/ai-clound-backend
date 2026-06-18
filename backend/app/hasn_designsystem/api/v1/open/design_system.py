"""设计系统（云端权威） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_designsystem.schema.design_system import GetDesignSystemDetail
from backend.app.hasn_designsystem.service.design_system_service import design_system_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取设计系统（云端权威）列表',
    dependencies=[DependsPagination],
    name='hasn_designsystem_open_get_design_system',
)
async def get_design_system(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetDesignSystemDetail]]:
    page_data = await design_system_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取设计系统（云端权威）详情',
    name='hasn_designsystem_open_get_design_system_detail',
)
async def get_design_system_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='设计系统（云端权威） ID')],
) -> ResponseSchemaModel[GetDesignSystemDetail]:
    design_system = await design_system_service.get(db=db, pk=pk)
    return response_base.success(data=design_system)
