"""服务号身份服务（§4.5）。

一个 source = 一个服务号 = 主人消息列表里一个会话条目（微信服务号效果）。
服务号是独立身份（前缀 sv_），按 (owner_id, kind, ref_id) 唯一 get-or-create。
仅 app/system/external 源建服务号；agent 源本身就是会话身份，不在此建号。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.notification.model.hasn_service_accounts import HasnServiceAccounts
from backend.database.db import uuid4_str

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 仅这些来源类型建服务号
SERVICE_ACCOUNT_KINDS = ('app', 'system', 'external')


class ServiceAccountService:
    @staticmethod
    async def get_or_create_for_source(
        db: AsyncSession,
        *,
        owner_id: str,
        source: dict[str, Any],
    ) -> HasnServiceAccounts | None:
        """按 source 解析/创建主人名下的服务号；agent/user 源返回 None（不建号）。"""
        kind = source.get('kind')
        if kind not in SERVICE_ACCOUNT_KINDS or not owner_id:
            return None
        ref_id = str(source.get('id') or kind)

        existing = (
            await db.execute(
                select(HasnServiceAccounts).where(
                    HasnServiceAccounts.owner_id == owner_id,
                    HasnServiceAccounts.kind == kind,
                    HasnServiceAccounts.ref_id == ref_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 刷新展示信息（来源可能更新名称/头像）
            display_name = source.get('display_name')
            avatar = source.get('avatar')
            if display_name and existing.display_name != display_name:
                existing.display_name = display_name
            if avatar and existing.avatar != avatar:
                existing.avatar = avatar
            await db.flush()
            return existing

        account = HasnServiceAccounts(
            sa_hasn_id=f'sv_{uuid4_str().replace("-", "")[:20]}',
            kind=kind,
            ref_id=ref_id,
            owner_id=owner_id,
            display_name=source.get('display_name') or ref_id,
            avatar=source.get('avatar') or '',
            verified=bool(kind == 'system'),
            status='active',
        )
        db.add(account)
        await db.flush()
        return account

    @staticmethod
    async def list_for_owner(db: AsyncSession, *, owner_id: str) -> list[dict[str, Any]]:
        """列出主人名下全部服务号（供 WebUI 解析服务号会话的名称/头像）。"""
        rows = (
            await db.execute(
                select(HasnServiceAccounts)
                .where(HasnServiceAccounts.owner_id == owner_id)
                .order_by(HasnServiceAccounts.created_time.desc())
            )
        ).scalars().all()
        return [
            {
                'sa_hasn_id': r.sa_hasn_id,
                'kind': r.kind,
                'display_name': r.display_name or r.sa_hasn_id,
                'avatar': r.avatar or '',
                'verified': bool(r.verified),
                'status': r.status,
            }
            for r in rows
        ]


service_account_service = ServiceAccountService()
