"""
凭证同步 API

@author Ysf
@date 2025-12-28
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db

from .schema import (
    CredentialDeleteResponse,
    CredentialInfo,
    CredentialListResponse,
    CredentialSyncRequest,
    CredentialSyncResponse,
)
from .service import CredentialService

router = APIRouter(prefix="/credentials", tags=["凭证同步"])


@router.post(
    "/sync",
    summary="同步凭证",
    description="将桌面端凭证同步到云端（端到端加密）",
)
async def sync_credential(
    request: CredentialSyncRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: dict = DependsJwtAuth,
) -> ResponseModel[CredentialSyncResponse]:
    """同步凭证到云端"""
    result = await CredentialService.sync_credential(
        db=db,
        user_id=current_user["id"],
        request=request,
    )
    return response_base.success(data=result)


@router.get(
    "/{platform}/{account_id}",
    summary="获取凭证",
    description="获取指定平台和账号的凭证",
)
async def get_credential(
    platform: str,
    account_id: str,
    sync_key_hash: Annotated[str, Header(alias="X-Sync-Key-Hash")],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: dict = DependsJwtAuth,
) -> ResponseModel[CredentialInfo | None]:
    """获取凭证"""
    result = await CredentialService.get_credential(
        db=db,
        user_id=current_user["id"],
        platform=platform,
        account_id=account_id,
        sync_key_hash=sync_key_hash,
    )
    if not result:
        return response_base.fail(msg="凭证不存在或密钥不匹配")
    return response_base.success(data=result)


@router.get(
    "",
    summary="列出凭证",
    description="列出用户的所有同步凭证",
)
async def list_credentials(
    platform: Annotated[str | None, Query(description="平台名称")] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = DependsJwtAuth,
) -> ResponseModel[CredentialListResponse]:
    """列出凭证"""
    result = await CredentialService.list_credentials(
        db=db,
        user_id=current_user["id"],
        platform=platform,
    )
    return response_base.success(data=result)


@router.delete(
    "/{platform}/{account_id}",
    summary="删除凭证",
    description="删除指定平台和账号的凭证",
)
async def delete_credential(
    platform: str,
    account_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: dict = DependsJwtAuth,
) -> ResponseModel[CredentialDeleteResponse]:
    """删除凭证"""
    result = await CredentialService.delete_credential(
        db=db,
        user_id=current_user["id"],
        platform=platform,
        account_id=account_id,
    )
    return response_base.success(data=result)
