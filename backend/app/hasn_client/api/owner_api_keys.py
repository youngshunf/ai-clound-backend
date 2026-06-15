"""M1 移动端 App — Owner API Key 当前值查询端点.

URL: GET /api/v1/app/owner_api_keys/current

依赖规范: docs/架构设计/移动端/05-凭据与安全详细设计.md §7.2 / §16.1。

实现策略(M1 最小可用):
- 认证通过后, 根据 request.user.id 查询该用户的 active HasnOwnerApiKeys 记录。
- 若存在 active 记录 → 返回 {owner_api_key, hasn_id, expires_at};
  数据库仅存 SHA256 哈希(key_hash), 因此 owner_api_key 字段在"已有记录"场景下暴露
  为空串, UI 应走轮换流程 (规范 §9.1)。
- 若不存在 active 记录 → 自动铸一次新 key (与 §7.2 "后端自动生成或返回明确错误"对齐),
  返回新鲜 plaintext。
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.schema import SchemaBase
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


class CurrentOwnerApiKeyDetail(SchemaBase):
    """/current 响应载荷."""

    owner_api_key: str = Field(
        description='Owner API Key 明文; 仅在首次铸造或已缓存明文时返回, 已有 active 记录但无缓存明文时返回空串, UI 应走轮换流程',
    )
    hasn_id: str = Field(description="Owner 绑定的 hasn_id (格式: 'h_xxx')")
    expires_at: Optional[datetime] = Field(None, description='到期时间 (null 表示长期有效)')


@dataclass(frozen=True)
class _CurrentKeyResult:
    owner_api_key: str
    hasn_id: str
    expires_at: Optional[datetime]


async def _generate_owner_api_key_plain() -> tuple[str, str]:
    """生成新 Owner API Key 明文 + SHA256 哈希 (格式: hasn_ok_<urlsafe48>)."""
    raw_key = f'hasn_ok_{secrets.token_urlsafe(48)}'
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


async def get_current_owner_api_key_for_user(db, user_id: int) -> _CurrentKeyResult:
    """业务入口: 查找或铸造用户 active Owner API Key.

    测试会直接 monkeypatch 本函数以解耦 DB。
    """
    from backend.app.hasn.model.hasn_humans import HasnHumans
    from backend.app.hasn.model.hasn_owner_api_keys import HasnOwnerApiKeys
    from backend.common.exception import errors

    result = await db.execute(
        select(HasnOwnerApiKeys)
        .where(HasnOwnerApiKeys.user_id == user_id)
        .where(HasnOwnerApiKeys.status == 'active')
        .order_by(HasnOwnerApiKeys.id.desc())
        .limit(1)
    )
    active = result.scalars().first()
    if active is not None:
        return _CurrentKeyResult(
            owner_api_key='',
            hasn_id=active.owner_id,
            expires_at=active.expires_at,
        )

    human_result = await db.execute(
        select(HasnHumans).where(HasnHumans.user_id == user_id).limit(1)
    )
    human = human_result.scalars().first()
    if human is None:
        raise errors.NotFoundError(msg='当前用户尚未开通 HASN 身份, 请先完成唤星账号初始化')

    owner_id = human.hasn_id
    raw_key, key_hash = await _generate_owner_api_key_plain()
    key_id = raw_key[:24]

    new_row = HasnOwnerApiKeys(
        key_id=key_id,
        user_id=user_id,
        owner_id=owner_id,
        key_name='mobile-auto-mint',
        key_hash=key_hash,
        status='active',
        scopes={},
    )
    db.add(new_row)
    await db.flush()

    return _CurrentKeyResult(
        owner_api_key=raw_key,
        hasn_id=owner_id,
        expires_at=None,
    )


@router.get(
    '/current',
    summary='获取当前 active Owner API Key (移动端 M1)',
    dependencies=[DependsJwtAuth],
)
async def get_current_owner_api_key(
    request: Request,
    db: CurrentSessionTransaction,
) -> ResponseSchemaModel[CurrentOwnerApiKeyDetail]:
    """返回当前登录用户的 active Owner API Key.

    成功: {data: {owner_api_key, hasn_id, expires_at}}
    401: 缺失/无效 JWT (由中间件 + DependsJwtAuth 共同拒绝)
    404: 用户尚未开通 HASN 身份 (无 hasn_humans 记录)
    """
    user_id = request.user.id
    result = await get_current_owner_api_key_for_user(db, user_id)
    payload = CurrentOwnerApiKeyDetail(
        owner_api_key=result.owner_api_key,
        hasn_id=result.hasn_id,
        expires_at=result.expires_at,
    )
    return response_base.success(data=payload)
