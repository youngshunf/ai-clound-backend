"""平台账号 API"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.project.schema.platform_account import (
    PlatformAccountCreate,
    PlatformAccountInfo,
    PlatformAccountSync,
    PlatformAccountUpdate,
)
from backend.app.project.service.platform_account_service import PlatformAccountService
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel

router = APIRouter()

# 独立路由器用于 /platform-accounts 端点（不需要 project_id 前缀）
user_accounts_router = APIRouter()


@router.get('/{project_id}/accounts', summary='获取项目账号列表')
async def get_accounts(
    project_id: Annotated[str, Path(description='项目 ID (UUID)')],
) -> ResponseSchemaModel[Sequence[PlatformAccountInfo]]:
    data = await PlatformAccountService.list(project_id)
    return ResponseSchemaModel(data=data)


@router.post('/{project_id}/accounts', summary='创建平台账号')
async def create_account(
    request: Request,
    project_id: Annotated[str, Path(description='项目 ID (UUID)')],
    obj: PlatformAccountCreate,
) -> ResponseSchemaModel[PlatformAccountInfo]:
    # 确保路径参数中的 project_id 与 body 中的一致
    obj.project_id = project_id
    data = await PlatformAccountService.create(request, obj)
    return ResponseSchemaModel(data=data)


@router.put('/{project_id}/accounts/{account_id}', summary='更新平台账号')
async def update_account(
    project_id: Annotated[str, Path(description='项目 ID (UUID)')],
    account_id: Annotated[str, Path(description='账号 ID (UUID)')],
    obj: PlatformAccountUpdate,
) -> ResponseModel:
    count = await PlatformAccountService.update(account_id, obj)
    return ResponseModel(data=count)


@router.delete('/{project_id}/accounts/{account_id}', summary='删除平台账号')
async def delete_account(
    project_id: Annotated[str, Path(description='项目 ID (UUID)')],
    account_id: Annotated[str, Path(description='账号 ID (UUID)')],
) -> ResponseModel:
    count = await PlatformAccountService.delete(account_id)
    return ResponseModel(data=count)


@router.post('/{project_id}/accounts/sync', summary='同步平台账号')
async def sync_account(
    request: Request,
    project_id: Annotated[str, Path(description='项目 ID (UUID)')],
    obj: PlatformAccountSync,
) -> ResponseSchemaModel[PlatformAccountInfo]:
    # 直接使用 project_id (UUID 字符串)
    obj.project_id = project_id
    data = await PlatformAccountService.sync(request, obj)
    return ResponseSchemaModel(data=data)


# ============= 用户级别的账号端点（用于桌面端同步） =============

@user_accounts_router.get('', summary='获取当前用户的所有平台账号')
async def get_user_accounts(
    request: Request,
) -> ResponseSchemaModel[Sequence[PlatformAccountInfo]]:
    """获取当前登录用户的所有平台账号（跨项目）

    用于桌面端登录后的云端数据同步
    """
    data = await PlatformAccountService.list_by_user(request.user.uuid)
    return ResponseSchemaModel(data=data)
