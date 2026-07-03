import uuid

from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_owner_api_keys import HasnOwnerApiKeys
from backend.app.hasn.schema.hasn_api_keys import CreateApiKeyRes
from backend.app.hasn.service.hasn_auth import _generate_owner_key
from backend.utils.timezone import timezone


class HasnApiKeyService:
    @staticmethod
    async def create_api_key(
        db: AsyncSession,
        user_id: int | None,
        user_hasn_id: str,
        name: str,
        scopes: dict | None = None,
        bound_node_id: str | None = None,
        expires_at=None,
    ) -> CreateApiKeyRes:
        """创建新的 Owner API Key"""
        key_id = f"ok_{uuid.uuid4().hex[:12]}"
        owner_api_key, key_hash = _generate_owner_key()

        record = HasnOwnerApiKeys(
            key_id=key_id,
            user_id=user_id,
            owner_id=user_hasn_id,
            key_name=name,
            key_hash=key_hash,
            scopes=scopes or {'bind_owner': True, 'register_agent': True},
            bound_node_id=bound_node_id,
            expires_at=expires_at,
            status='active',
        )
        db.add(record)
        await db.flush()

        return CreateApiKeyRes(
            key_id=record.key_id,
            key_name=record.key_name,
            owner_id=record.owner_id,
            status=record.status,
            scopes=record.scopes,
            bound_node_id=record.bound_node_id,
            expires_at=record.expires_at,
            created_time=record.created_time,
            last_seen_at=record.last_used_at,
            owner_api_key=owner_api_key,
        )

    @staticmethod
    async def list_api_keys(
        db: AsyncSession,
        user_hasn_id: str,
    ) -> Sequence[HasnOwnerApiKeys]:
        """列出用户的所有 Owner API Key"""
        result = await db.execute(
            select(HasnOwnerApiKeys).where(
                HasnOwnerApiKeys.owner_id == user_hasn_id,
                HasnOwnerApiKeys.status != 'deleted',
            ).order_by(HasnOwnerApiKeys.created_time.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def delete_api_key(
        db: AsyncSession,
        user_hasn_id: str,
        key_id: str,
    ) -> None:
        """吊销 Owner API Key"""
        result = await db.execute(
            select(HasnOwnerApiKeys).where(
                HasnOwnerApiKeys.key_id == key_id,
                HasnOwnerApiKeys.owner_id == user_hasn_id,
                HasnOwnerApiKeys.status == 'active',
            )
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="API Key 不存在")

        record.status = 'revoked'
        record.revoked_at = timezone.now()
        record.revoke_reason = 'manual_revoke'
        await db.flush()


hasn_api_key_service = HasnApiKeyService()
