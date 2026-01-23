"""
凭证同步服务

@author Ysf
@date 2025-12-28
"""

import base64

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import SyncedCredential
from .schema import (
    CredentialDeleteResponse,
    CredentialInfo,
    CredentialListResponse,
    CredentialSyncRequest,
    CredentialSyncResponse,
)


class CredentialService:
    """凭证同步服务"""

    @staticmethod
    async def sync_credential(
        db: AsyncSession,
        user_id: int,
        request: CredentialSyncRequest,
    ) -> CredentialSyncResponse:
        """
        同步凭证到云端

        Args:
            db: 数据库会话
            user_id: 用户ID
            request: 同步请求

        Returns:
            同步响应
        """
        # 解码加密数据
        try:
            encrypted_bytes = base64.b64decode(request.encrypted_data)
        except Exception:
            return CredentialSyncResponse(
                success=False,
                platform=request.platform,
                account_id=request.account_id,
                version=0,
                message="加密数据格式错误",
            )

        # 查找现有凭证
        stmt = select(SyncedCredential).where(
            and_(
                SyncedCredential.user_id == user_id,
                SyncedCredential.platform == request.platform,
                SyncedCredential.account_id == request.account_id,
            )
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # 版本冲突检测
            if request.version <= existing.version:
                return CredentialSyncResponse(
                    success=False,
                    platform=request.platform,
                    account_id=request.account_id,
                    version=existing.version,
                    message=f"版本冲突，服务端版本: {existing.version}",
                )

            # 更新现有凭证
            existing.account_name = request.account_name
            existing.encrypted_data = encrypted_bytes
            existing.sync_key_hash = request.sync_key_hash
            existing.client_id = request.client_id
            existing.version = request.version
            await db.commit()

            return CredentialSyncResponse(
                success=True,
                platform=request.platform,
                account_id=request.account_id,
                version=request.version,
                message="凭证更新成功",
            )
        # 创建新凭证
        credential = SyncedCredential(
            user_id=user_id,
            platform=request.platform,
            account_id=request.account_id,
            account_name=request.account_name,
            encrypted_data=encrypted_bytes,
            sync_key_hash=request.sync_key_hash,
            client_id=request.client_id,
            version=request.version,
        )
        db.add(credential)
        await db.commit()

        return CredentialSyncResponse(
            success=True,
            platform=request.platform,
            account_id=request.account_id,
            version=request.version,
            message="凭证同步成功",
        )

    @staticmethod
    async def get_credential(
        db: AsyncSession,
        user_id: int,
        platform: str,
        account_id: str,
        sync_key_hash: str,
    ) -> CredentialInfo | None:
        """
        获取凭证

        Args:
            db: 数据库会话
            user_id: 用户ID
            platform: 平台名称
            account_id: 账号ID
            sync_key_hash: 同步密钥哈希

        Returns:
            凭证信息或None
        """
        stmt = select(SyncedCredential).where(
            and_(
                SyncedCredential.user_id == user_id,
                SyncedCredential.platform == platform,
                SyncedCredential.account_id == account_id,
                SyncedCredential.sync_key_hash == sync_key_hash,
                SyncedCredential.status == 1,
            )
        )
        result = await db.execute(stmt)
        credential = result.scalar_one_or_none()

        if not credential:
            return None

        return CredentialInfo(
            platform=credential.platform,
            account_id=credential.account_id,
            account_name=credential.account_name,
            encrypted_data=base64.b64encode(credential.encrypted_data).decode(),
            version=credential.version,
            created_time=credential.created_time,
            updated_time=credential.updated_time,
        )

    @staticmethod
    async def list_credentials(
        db: AsyncSession,
        user_id: int,
        platform: str | None = None,
    ) -> CredentialListResponse:
        """
        列出用户凭证

        Args:
            db: 数据库会话
            user_id: 用户ID
            platform: 平台名称（可选）

        Returns:
            凭证列表
        """
        conditions = [
            SyncedCredential.user_id == user_id,
            SyncedCredential.status == 1,
        ]
        if platform:
            conditions.append(SyncedCredential.platform == platform)

        stmt = select(SyncedCredential).where(and_(*conditions))
        result = await db.execute(stmt)
        credentials = result.scalars().all()

        items = [
            CredentialInfo(
                platform=c.platform,
                account_id=c.account_id,
                account_name=c.account_name,
                encrypted_data=base64.b64encode(c.encrypted_data).decode(),
                version=c.version,
                created_time=c.created_time,
                updated_time=c.updated_time,
            )
            for c in credentials
        ]

        return CredentialListResponse(
            credentials=items,
            total=len(items),
        )

    @staticmethod
    async def delete_credential(
        db: AsyncSession,
        user_id: int,
        platform: str,
        account_id: str,
    ) -> CredentialDeleteResponse:
        """
        删除凭证

        Args:
            db: 数据库会话
            user_id: 用户ID
            platform: 平台名称
            account_id: 账号ID

        Returns:
            删除响应
        """
        stmt = select(SyncedCredential).where(
            and_(
                SyncedCredential.user_id == user_id,
                SyncedCredential.platform == platform,
                SyncedCredential.account_id == account_id,
            )
        )
        result = await db.execute(stmt)
        credential = result.scalar_one_or_none()

        if not credential:
            return CredentialDeleteResponse(
                success=False,
                platform=platform,
                account_id=account_id,
                message="凭证不存在",
            )

        await db.delete(credential)
        await db.commit()

        return CredentialDeleteResponse(
            success=True,
            platform=platform,
            account_id=account_id,
            message="凭证删除成功",
        )
