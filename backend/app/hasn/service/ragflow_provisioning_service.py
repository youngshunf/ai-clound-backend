from __future__ import annotations

import secrets

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx
import sqlalchemy as sa

from backend.app.admin.model.user import User
from backend.app.hasn.model import HasnAppCredential, HasnAppInstance
from backend.app.hasn.service.ragflow_client import RAGFlowClient
from backend.app.hasn.util.rsa_pwd import rsa_encrypt_password
from backend.app.llm.core.encryption import key_encryption
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

# 知识库在统一应用平台底座中的 app_id（实施 03：knowledge 实例/凭据收编进 hasn_app_*）
KNOWLEDGE_APP_ID = 'knowledge'


def _instance_public_pem(instance: HasnAppInstance) -> str:
    return (instance.config or {}).get('public_pem', '')


def _instance_embd_id(instance: HasnAppInstance) -> str | None:
    return (instance.config or {}).get('default_embd_id')


def _instance_llm_id(instance: HasnAppInstance) -> str | None:
    return (instance.config or {}).get('default_llm_id')


class CredentialRepository(Protocol):
    async def get_instance(self, instance_id: int) -> None: ...
    async def get_user(self, user_id: int) -> None: ...
    async def upsert_pending_credential(self, *, user_id: int, instance_id: int, reason: str) -> None: ...
    async def upsert_active_credential(
        self,
        *,
        user_id: int,
        instance_id: int,
        ragflow_user_id: str,
        ragflow_tenant_id: str,
        api_key: str,
    ) -> None: ...
    async def mark_revoked(self, credential_id: int) -> None: ...
    async def get_credential(self, credential_id: int) -> None: ...


@dataclass(frozen=True)
class RAGFlowCredentialForRevoke:
    id: int
    user_id: int
    instance_id: int
    ragflow_user_id: str
    ragflow_tenant_id: str
    api_key: str
    status: str
    instance: HasnAppInstance


@dataclass(frozen=True)
class ProvisionedCredential:
    user_id: int
    instance_id: int
    ragflow_user_id: str
    ragflow_tenant_id: str
    status: str


class SqlAlchemyRAGFlowCredentialRepository:
    """知识库 provisioning 持久化层。

    实施 03 收编后读写统一应用平台底座 ``hasn_app_instance`` / ``hasn_app_credential``
    （app_id='knowledge'），加密统一走 ``key_encryption``（credential_ref 密文串），
    RAGFlow 私有字段（ragflow_user_id/ragflow_tenant_id）下沉 ``config``。
    provisioning 业务语义（POST /users → token → embd → dataset）不变。
    """

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        self.session_factory = session_factory or async_db_session

    async def get_instance(self, instance_id: int) -> HasnAppInstance:
        async with self.session_factory() as db:
            instance = (
                await db.execute(sa.select(HasnAppInstance).where(HasnAppInstance.id == instance_id))
            ).scalar_one_or_none()
            if instance is None:
                raise RuntimeError(f'app instance {instance_id} not found')
            return instance

    async def get_user(self, user_id: int):
        async with self.session_factory() as db:
            user = (await db.execute(sa.select(User).where(User.id == user_id))).scalar_one_or_none()
            if user is None:
                raise RuntimeError(f'user {user_id} not found')
            return user

    async def upsert_pending_credential(self, *, user_id: int, instance_id: int, reason: str):
        async with self.session_factory.begin() as db:
            credential = await self._find_credential(db, user_id=user_id, instance_id=instance_id)
            if credential is None:
                credential = HasnAppCredential(
                    app_id=KNOWLEDGE_APP_ID,
                    user_id=user_id,
                    app_instance_id=instance_id,
                    credential_ref='',
                    status='pending',
                    last_error=reason,
                    config={},
                )
                db.add(credential)
            elif credential.status != 'active':
                credential.status = 'pending'
                credential.last_error = reason
            await db.flush()
            return credential

    async def upsert_active_credential(
        self,
        *,
        user_id: int,
        instance_id: int,
        ragflow_user_id: str,
        ragflow_tenant_id: str,
        api_key: str,
    ):
        async with self.session_factory.begin() as db:
            credential = await self._find_credential(db, user_id=user_id, instance_id=instance_id)
            config = {'ragflow_user_id': ragflow_user_id, 'ragflow_tenant_id': ragflow_tenant_id}
            if credential is None:
                credential = HasnAppCredential(
                    app_id=KNOWLEDGE_APP_ID,
                    user_id=user_id,
                    app_instance_id=instance_id,
                    credential_ref=key_encryption.encrypt(api_key),
                    status='active',
                    last_error=None,
                    config=config,
                )
                db.add(credential)
            else:
                credential.credential_ref = key_encryption.encrypt(api_key)
                credential.status = 'active'
                credential.last_error = None
                credential.config = config
            await db.flush()
            return ProvisionedCredential(
                user_id=user_id,
                instance_id=instance_id,
                ragflow_user_id=ragflow_user_id,
                ragflow_tenant_id=ragflow_tenant_id,
                status='active',
            )

    async def mark_revoked(self, credential_id: int) -> None:
        async with self.session_factory.begin() as db:
            credential = (
                await db.execute(sa.select(HasnAppCredential).where(HasnAppCredential.id == credential_id))
            ).scalar_one_or_none()
            if credential is None:
                return
            credential.status = 'revoked'
            credential.last_error = None

    async def get_credential(self, credential_id: int):
        async with self.session_factory() as db:
            credential = (
                await db.execute(sa.select(HasnAppCredential).where(HasnAppCredential.id == credential_id))
            ).scalar_one_or_none()
            if credential is None:
                raise RuntimeError(f'app credential {credential_id} not found')
            instance = (
                await db.execute(
                    sa.select(HasnAppInstance).where(HasnAppInstance.id == credential.app_instance_id)
                )
            ).scalar_one_or_none()
            if instance is None:
                raise RuntimeError(f'app instance {credential.app_instance_id} not found')
            config = credential.config or {}
            return RAGFlowCredentialForRevoke(
                id=credential.id,
                user_id=credential.user_id,
                instance_id=credential.app_instance_id,
                ragflow_user_id=config.get('ragflow_user_id', ''),
                ragflow_tenant_id=config.get('ragflow_tenant_id', ''),
                api_key=key_encryption.decrypt(credential.credential_ref) if credential.credential_ref else '',
                status=credential.status,
                instance=instance,
            )

    @staticmethod
    async def _find_credential(db, *, user_id: int, instance_id: int):
        return (
            await db.execute(
                sa.select(HasnAppCredential).where(
                    HasnAppCredential.user_id == user_id,
                    HasnAppCredential.app_instance_id == instance_id,
                )
            )
        ).scalar_one_or_none()


class RAGFlowProvisioningService:
    def __init__(self, repository: CredentialRepository | None = None) -> None:
        self.repository = repository

    async def provision_one(self, user_id: int, instance_id: int):
        if self.repository is None:
            raise RuntimeError('credential repository is required for provision_one')
        instance = await self.repository.get_instance(instance_id)
        if instance.status != 'active':
            return await self.repository.upsert_pending_credential(
                user_id=user_id,
                instance_id=instance_id,
                reason='instance not yet configured',
            )

        user = await self.repository.get_user(user_id)
        client = RAGFlowClient(instance.endpoint)
        password = secrets.token_urlsafe(32)
        encrypted_password = rsa_encrypt_password(password, _instance_public_pem(instance))
        response = await client.request(
            'POST',
            '/api/v1/users',
            json={
                'email': f'u-{user.id}@ragflow.internal',
                'password': encrypted_password,
                'nickname': getattr(user, 'nickname', None) or f'user-{user.id}',
            },
        )
        ragflow_user_id = response.body['data']['id']
        jwt = response.headers.get('Authorization') or response.headers.get('authorization')
        if not jwt:
            raise RuntimeError('RAGFlow registration response missing Authorization header')
        token = (await client.post('/api/v1/system/tokens', headers={'Authorization': jwt}))['data']['token']
        await client.patch(
            '/api/v1/users/me/models',
            json={
                'tenant_id': ragflow_user_id,
                'embd_id': _instance_embd_id(instance),
                'llm_id': _instance_llm_id(instance) or '',
                'asr_id': '',
                'img2txt_id': '',
            },
            headers={'Authorization': f'Bearer {token}'},
        )
        await client.post(
            '/api/v1/datasets',
            json={'name': '我的知识库'},
            headers={'Authorization': f'Bearer {token}'},
        )
        return await self.repository.upsert_active_credential(
            user_id=user_id,
            instance_id=instance_id,
            ragflow_user_id=ragflow_user_id,
            ragflow_tenant_id=ragflow_user_id,
            api_key=token,
        )

    async def revoke_one(self, credential_id: int) -> None:
        if self.repository is None:
            raise RuntimeError('credential repository is required for revoke_one')
        credential = await self.repository.get_credential(credential_id)
        if credential.status == 'revoked' or not credential.api_key:
            await self.repository.mark_revoked(credential_id)
            return
        client = RAGFlowClient(credential.instance.endpoint)
        try:
            await client.delete(
                f'/api/v1/system/tokens/{credential.api_key}',
                headers={'Authorization': f'Bearer {credential.api_key}'},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (401, 404):
                raise
        await self.repository.mark_revoked(credential_id)


ragflow_provisioning_service = RAGFlowProvisioningService(SqlAlchemyRAGFlowCredentialRepository())
