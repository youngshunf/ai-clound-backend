"""
公共 Owner Key 认证模块

从 HASN Owner API Key 体系中提取的公共认证依赖。
可用于：文档工具、云函数、未来所有需要用户级认证的 API。

认证方式:
  - Authorization: OwnerKey hasn_ok_xxx
  - Authorization: Bearer hasn_ok_xxx （兼容标准格式）

路由前缀: /api/v1/{module}/user/

@author Ysf (auto-generated)
"""
import hashlib

from fastapi import Depends, HTTPException, Request
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnOwnerApiKeys
from backend.database.db import CurrentSession
from backend.utils.timezone import timezone


async def verify_owner_key_standalone(
    owner_api_key: str,
    db: AsyncSession,
) -> HasnOwnerApiKeys:
    """
    仅凭 Owner API Key 验证用户身份（不需要预知 owner_hasn_id）。

    与 hasn_auth.verify_owner_api_key() 的区别：
    - 不需要 owner_hasn_id 参数（仅凭 key_hash 查询）
    - 可独立于 HASN 节点上下文使用
    - 用于文档、云函数等公共 API 认证

    :param owner_api_key: hasn_ok_xxx 格式的 API Key
    :param db: 数据库会话
    :return: HasnOwnerApiKeys 记录（含 user_id, owner_id 等）
    :raises HTTPException: 验证失败
    """
    if not owner_api_key.startswith('hasn_ok_'):
        raise HTTPException(status_code=401, detail='无效的 API Key 格式（期望 hasn_ok_ 前缀）')

    key_hash = hashlib.sha256(owner_api_key.encode()).hexdigest()

    result = await db.execute(
        select(HasnOwnerApiKeys).where(
            HasnOwnerApiKeys.key_hash == key_hash,
            HasnOwnerApiKeys.status == 'active',
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=401, detail='API Key 无效或已停用')

    # 过期检查
    if key.expires_at and key.expires_at <= timezone.now():
        raise HTTPException(status_code=401, detail='API Key 已过期')

    # user_id 必须存在
    if key.user_id is None:
        raise HTTPException(status_code=401, detail='API Key 未绑定平台用户')

    # 更新最后使用时间
    key.last_used_at = timezone.now()
    await db.flush()

    return key


async def owner_key_auth(
    request: Request,
    db: CurrentSession,
) -> int:
    """
    FastAPI 依赖注入：从 Authorization header 提取 Owner Key 并返回 user_id。

    支持两种格式::

        Authorization: OwnerKey hasn_ok_xxx
        Authorization: Bearer hasn_ok_xxx

    用法::

        @router.get("/xxx")
        async def xxx(user_id: int = DependsOwnerKeyAuth):
            # user_id 已验证，直接使用
            ...

    :return: user_id (int)
    """
    authorization = request.headers.get('Authorization')
    if not authorization:
        raise HTTPException(status_code=401, detail='缺少认证信息，请提供 Owner API Key')

    scheme, credentials = get_authorization_scheme_param(authorization)

    if scheme.lower() not in ('ownerkey', 'bearer'):
        raise HTTPException(status_code=401, detail=f'不支持的认证方式: {scheme}，请使用 OwnerKey 或 Bearer')

    if not credentials.startswith('hasn_ok_'):
        raise HTTPException(status_code=401, detail='请使用 Owner API Key (hasn_ok_xxx 格式)')

    key = await verify_owner_key_standalone(credentials, db)
    if key.user_id is None:
        raise HTTPException(status_code=401, detail='API Key 未绑定平台用户')
    return key.user_id


# FastAPI 依赖注入快捷方式
DependsOwnerKeyAuth = Depends(owner_key_auth)
