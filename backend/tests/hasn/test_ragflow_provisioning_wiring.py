from __future__ import annotations

import base64

from types import SimpleNamespace
from typing import TYPE_CHECKING, NoReturn

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.hasn.service.ragflow_client import RAGFlowResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class _Base(DeclarativeBase):
    pass


class UserStub(_Base):
    __tablename__ = 'sys_user'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(sa.String(64), default='')
    nickname: Mapped[str] = mapped_column(sa.String(64), default='')


class MembershipStub(_Base):
    __tablename__ = 'hasn_enterprise_membership'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    user_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    role: Mapped[str] = mapped_column(sa.String(16), default='member')
    status: Mapped[str] = mapped_column(sa.String(16), default='pending')


# 实施 03 收编后：知识库实例/凭据底层是统一应用平台底座 hasn_app_instance/hasn_app_credential。
# 桩模型镜像新表（app_id='knowledge'，RAGFlow 私有字段在 config，密文在 credential_ref 字符串）。
class RagflowInstanceStub(_Base):
    __tablename__ = 'hasn_app_instance'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='knowledge')
    scope: Mapped[str] = mapped_column(sa.String(16), default='public')
    enterprise_id: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    endpoint: Mapped[str | None] = mapped_column(sa.String(512), default=None)
    transport_default: Mapped[str] = mapped_column(sa.String(24), default='daemon_direct')
    credential_ref: Mapped[str] = mapped_column(sa.String(512), default='')
    status: Mapped[str] = mapped_column(sa.String(16), default='pending_config')
    config: Mapped[dict] = mapped_column(sa.JSON, default=dict)


class RagflowCredentialStub(_Base):
    __tablename__ = 'hasn_app_credential'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='knowledge')
    user_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    app_instance_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    credential_ref: Mapped[str] = mapped_column(sa.String(512), default='')
    status: Mapped[str] = mapped_column(sa.String(16), default='pending')
    last_error: Mapped[str | None] = mapped_column(sa.Text, default=None)
    config: Mapped[dict] = mapped_column(sa.JSON, default=dict)


class CapturingProvisioningService:
    def __init__(self) -> None:
        self.provisioned: list[tuple[int, int]] = []
        self.revoked: list[int] = []

    async def provision_one(self, user_id: int, instance_id: int) -> None:
        self.provisioned.append((user_id, instance_id))

    async def revoke_one(self, credential_id: int) -> None:
        self.revoked.append(credential_id)


class InMemoryCredentialRepository:
    def __init__(self, *, instance: object, user: object | None = None, credential: object | None = None) -> None:
        self.instance = instance
        self.user = user or SimpleNamespace(id=201, nickname='Provisioned User')
        self.credential = credential
        self.pending_calls: list[dict[str, object]] = []
        self.active_calls: list[dict[str, object]] = []
        self.revoked: list[int] = []

    async def get_instance(self, instance_id: int) -> object:
        assert instance_id == self.instance.id
        return self.instance

    async def get_user(self, user_id: int) -> object:
        assert user_id == self.user.id
        return self.user

    async def upsert_pending_credential(self, *, user_id: int, instance_id: int, reason: str) -> dict[str, object]:
        payload = {'user_id': user_id, 'instance_id': instance_id, 'reason': reason}
        self.pending_calls.append(payload)
        return payload

    async def upsert_active_credential(
        self,
        *,
        user_id: int,
        instance_id: int,
        ragflow_user_id: str,
        ragflow_tenant_id: str,
        api_key: str,
    ) -> dict[str, object]:
        payload = {
            'user_id': user_id,
            'instance_id': instance_id,
            'ragflow_user_id': ragflow_user_id,
            'ragflow_tenant_id': ragflow_tenant_id,
            'api_key': api_key,
        }
        self.active_calls.append(payload)
        return payload

    async def mark_revoked(self, credential_id: int) -> None:
        self.revoked.append(credential_id)

    async def get_credential(self, credential_id: int) -> object:
        assert self.credential is not None
        assert credential_id == self.credential.id
        return self.credential


class CapturingRAGFlowProvisionClient:
    instances: list[CapturingRAGFlowProvisionClient] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.__class__.instances.append(self)

    async def request(self, method: str, path: str, *, json: dict[str, object] | None = None) -> RAGFlowResponse:
        self.calls.append((method, path, {'json': json}))

        return RAGFlowResponse(
            status_code=200,
            headers={'Authorization': 'Bearer registration-jwt'},
            body={'data': {'id': 'ragflow-user-201'}},
        )

    async def post(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(('POST', path, {'json': json, 'headers': headers}))
        if path == '/api/v1/system/tokens':
            return {'data': {'token': 'ragflow-api-token'}}
        if path == '/api/v1/datasets':
            return {'data': {'id': 'dataset-1'}}
        raise AssertionError(f'unexpected POST {path}')

    async def patch(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(('PATCH', path, {'json': json, 'headers': headers}))
        return {'data': {'ok': True}}


class FailingDeleteRAGFlowClient:
    def __init__(self, base_url: str, status_code: int) -> None:
        self.base_url = base_url
        self.status_code = status_code
        self.deleted: list[tuple[str, dict[str, str] | None]] = []

    async def delete(self, path: str, *, headers: dict[str, str] | None = None) -> NoReturn:
        self.deleted.append((path, headers))
        request = httpx.Request('DELETE', f'{self.base_url}{path}')
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError('delete failed', request=request, response=response)


@pytest_asyncio.fixture
async def ragflow_sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    import backend.app.hasn.service.ragflow_provisioning_service as provisioning_mod
    import backend.app.hasn.service.ragflow_subscriber as subscriber_mod

    replacements = {
        'User': UserStub,
        'HasnEnterpriseMembership': MembershipStub,
        'HasnAppInstance': RagflowInstanceStub,
        'HasnAppCredential': RagflowCredentialStub,
    }
    for name, replacement in replacements.items():
        if hasattr(provisioning_mod, name):
            monkeypatch.setattr(provisioning_mod, name, replacement, raising=True)
        if hasattr(subscriber_mod, name):
            monkeypatch.setattr(subscriber_mod, name, replacement, raising=True)

    engine = create_async_engine('sqlite+aiosqlite:///:memory:', future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(provisioning_mod, 'async_db_session', sessionmaker, raising=False)
    monkeypatch.setattr(subscriber_mod, 'async_db_session', sessionmaker, raising=True)
    try:
        yield sessionmaker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlalchemy_actions_delegate_member_lifecycle_to_provisioning_service(
    ragflow_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from backend.app.hasn.service.ragflow_subscriber import SqlAlchemyRAGFlowActions

    async with ragflow_sessionmaker.begin() as db:
        instance = RagflowInstanceStub(
            scope='enterprise',
            enterprise_id=42,
            endpoint='https://knowledge.example',
            config={'public_pem': 'pem'},
            status='active',
        )
        db.add(instance)
        await db.flush()
        credential = RagflowCredentialStub(
            user_id=99,
            app_instance_id=instance.id,
            credential_ref='encrypted',
            config={'ragflow_user_id': 'u-99', 'ragflow_tenant_id': 't-99'},
            status='active',
        )
        db.add(credential)
        await db.flush()
        instance_id = instance.id
        credential_id = credential.id

    provisioning = CapturingProvisioningService()
    actions = SqlAlchemyRAGFlowActions(provisioning_service=provisioning)

    await actions.provision_member(enterprise_id=42, user_id=99)
    await actions.revoke_member(enterprise_id=42, user_id=99)

    assert provisioning.provisioned == [(99, instance_id)]
    assert provisioning.revoked == [credential_id]


@pytest.mark.asyncio
async def test_revoke_member_pushes_credentials_changed_after_revoking(
    ragflow_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """安全关键（设计 §2.3/§9.1）：撤销成员凭据后必须推 KnowledgeCredentialsChanged，
    否则 daemon 缓存的本地明文 key 不会被丢弃。"""
    from backend.app.hasn.service.ragflow_subscriber import SqlAlchemyRAGFlowActions

    async with ragflow_sessionmaker.begin() as db:
        instance = RagflowInstanceStub(
            scope='enterprise',
            enterprise_id=55,
            endpoint='https://knowledge.example',
            config={'public_pem': 'pem'},
            status='active',
        )
        db.add(instance)
        await db.flush()
        db.add(
            RagflowCredentialStub(
                user_id=77,
                app_instance_id=instance.id,
                credential_ref='encrypted',
                config={'ragflow_user_id': 'u-77', 'ragflow_tenant_id': 't-77'},
                status='active',
            )
        )
        await db.flush()

    actions = SqlAlchemyRAGFlowActions(provisioning_service=CapturingProvisioningService())
    notified: list[int] = []

    async def record(*, user_id: int) -> None:
        notified.append(user_id)

    actions.notify_credentials_changed = record  # type: ignore[method-assign]
    await actions.revoke_member(enterprise_id=55, user_id=77)

    assert notified == [77]  # 撤销后推送了一次

    # 对没有任何凭据的成员撤销时不应误推（避免噪声 WS）— credential_ids 为空走 no-op 守卫
    notified.clear()
    await actions.revoke_member(enterprise_id=55, user_id=88)  # 88 在该实例无凭据
    assert notified == []


@pytest.mark.asyncio
async def test_compensation_scan_provisions_approved_members_for_active_instances(
    ragflow_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from backend.app.hasn.service.ragflow_subscriber import SqlAlchemyRAGFlowActions

    async with ragflow_sessionmaker.begin() as db:
        instance = RagflowInstanceStub(
            scope='enterprise',
            enterprise_id=7,
            endpoint='https://knowledge.example',
            config={'public_pem': 'pem'},
            status='active',
        )
        db.add_all([
            instance,
            MembershipStub(enterprise_id=7, user_id=101, role='member', status='approved'),
            MembershipStub(enterprise_id=7, user_id=102, role='member', status='pending'),
        ])
        await db.flush()
        instance_id = instance.id

    provisioning = CapturingProvisioningService()
    actions = SqlAlchemyRAGFlowActions(provisioning_service=provisioning)

    processed = await actions.compensate_pending_credentials()

    assert processed == 1
    assert provisioning.provisioned == [(101, instance_id)]


@pytest.mark.asyncio
async def test_sqlalchemy_credential_repository_encrypts_and_decrypts_api_key(
    ragflow_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from backend.app.hasn.service.ragflow_provisioning_service import (
        SqlAlchemyRAGFlowCredentialRepository,
    )

    async with ragflow_sessionmaker.begin() as db:
        db.add(UserStub(username='u-201', nickname='User 201'))
        instance = RagflowInstanceStub(
            scope='public',
            enterprise_id=None,
            endpoint='https://knowledge.example',
            config={'public_pem': 'pem'},
            status='active',
        )
        db.add(instance)
        await db.flush()
        user_id = 1
        instance_id = instance.id

    repository = SqlAlchemyRAGFlowCredentialRepository(ragflow_sessionmaker)
    await repository.upsert_active_credential(
        user_id=user_id,
        instance_id=instance_id,
        ragflow_user_id='u-ragflow',
        ragflow_tenant_id='t-ragflow',
        api_key='ragflow-secret-token',
    )

    async with ragflow_sessionmaker() as db:
        row = (
            await db.execute(
                sa.select(RagflowCredentialStub).where(
                    RagflowCredentialStub.user_id == user_id,
                    RagflowCredentialStub.app_instance_id == instance_id,
                )
            )
        ).scalar_one()
        assert row.status == 'active'
        assert 'ragflow-secret-token' not in row.credential_ref

    credential = await repository.get_credential(row.id)

    assert credential.api_key == 'ragflow-secret-token'
    assert credential.instance.id == instance_id


@pytest.mark.asyncio
async def test_provision_one_marks_pending_when_instance_is_not_active() -> None:
    from backend.app.hasn.service.ragflow_provisioning_service import RAGFlowProvisioningService

    instance = SimpleNamespace(id=501, status='pending_config', endpoint='https://knowledge.example', config={})
    repository = InMemoryCredentialRepository(instance=instance)

    result = await RAGFlowProvisioningService(repository).provision_one(user_id=201, instance_id=501)

    assert result == {
        'user_id': 201,
        'instance_id': 501,
        'reason': 'instance not yet configured',
    }
    assert repository.pending_calls == [result]
    assert repository.active_calls == []


@pytest.mark.asyncio
async def test_provision_one_registers_user_sets_models_and_creates_default_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.hasn.service.ragflow_provisioning_service as provisioning_mod

    from backend.app.hasn.service.ragflow_provisioning_service import RAGFlowProvisioningService

    CapturingRAGFlowProvisionClient.instances = []
    monkeypatch.setattr(provisioning_mod, 'RAGFlowClient', CapturingRAGFlowProvisionClient)
    monkeypatch.setattr(provisioning_mod.secrets, 'token_urlsafe', lambda _length: 'generated-password')
    monkeypatch.setattr(
        provisioning_mod,
        'rsa_encrypt_password',
        lambda plain, public_pem: f'encrypted:{plain}:{public_pem}',
    )

    instance = SimpleNamespace(
        id=502,
        status='active',
        endpoint='https://knowledge.example',
        config={
            'public_pem': 'public-pem',
            'default_embd_id': 'bge-m3@HuanxingNewApi',
            'default_llm_id': 'gpt-test',
        },
    )
    user = SimpleNamespace(id=201, nickname='')
    repository = InMemoryCredentialRepository(instance=instance, user=user)

    result = await RAGFlowProvisioningService(repository).provision_one(user_id=201, instance_id=502)

    assert result['api_key'] == 'ragflow-api-token'
    assert repository.active_calls == [
        {
            'user_id': 201,
            'instance_id': 502,
            'ragflow_user_id': 'ragflow-user-201',
            'ragflow_tenant_id': 'ragflow-user-201',
            'api_key': 'ragflow-api-token',
        }
    ]
    client = CapturingRAGFlowProvisionClient.instances[0]
    assert client.base_url == 'https://knowledge.example'
    assert client.calls == [
        (
            'POST',
            '/api/v1/users',
            {
                'json': {
                    'email': 'u-201@ragflow.internal',
                    'password': 'encrypted:generated-password:public-pem',
                    'nickname': 'user-201',
                }
            },
        ),
        (
            'POST',
            '/api/v1/system/tokens',
            {'json': None, 'headers': {'Authorization': 'Bearer registration-jwt'}},
        ),
        (
            'PATCH',
            '/api/v1/users/me/models',
            {
                'json': {
                    'tenant_id': 'ragflow-user-201',
                    'embd_id': 'bge-m3@HuanxingNewApi',
                    'llm_id': 'gpt-test',
                    'asr_id': '',
                    'img2txt_id': '',
                },
                'headers': {'Authorization': 'Bearer ragflow-api-token'},
            },
        ),
        (
            'POST',
            '/api/v1/datasets',
            {
                'json': {'name': '我的知识库'},
                'headers': {'Authorization': 'Bearer ragflow-api-token'},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_provision_one_requires_repository() -> None:
    from backend.app.hasn.service.ragflow_provisioning_service import RAGFlowProvisioningService

    with pytest.raises(RuntimeError, match='credential repository is required'):
        await RAGFlowProvisioningService().provision_one(user_id=1, instance_id=1)


@pytest.mark.asyncio
async def test_revoke_one_marks_revoked_without_remote_call_when_already_revoked() -> None:
    from backend.app.hasn.service.ragflow_provisioning_service import RAGFlowProvisioningService

    credential = SimpleNamespace(
        id=11,
        status='revoked',
        api_key='',
        instance=SimpleNamespace(endpoint='https://knowledge.example'),
    )
    repository = InMemoryCredentialRepository(instance=credential.instance, credential=credential)

    await RAGFlowProvisioningService(repository).revoke_one(credential_id=11)

    assert repository.revoked == [11]


@pytest.mark.asyncio
async def test_revoke_one_treats_remote_401_as_already_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.hasn.service.ragflow_provisioning_service as provisioning_mod

    from backend.app.hasn.service.ragflow_provisioning_service import RAGFlowProvisioningService

    client = FailingDeleteRAGFlowClient('https://knowledge.example', status_code=401)
    monkeypatch.setattr(provisioning_mod, 'RAGFlowClient', lambda _base_url: client)
    credential = SimpleNamespace(
        id=12,
        status='active',
        api_key='old-token',
        instance=SimpleNamespace(endpoint='https://knowledge.example'),
    )
    repository = InMemoryCredentialRepository(instance=credential.instance, credential=credential)

    await RAGFlowProvisioningService(repository).revoke_one(credential_id=12)

    assert client.deleted == [
        ('/api/v1/system/tokens/old-token', {'Authorization': 'Bearer old-token'}),
    ]
    assert repository.revoked == [12]


@pytest.mark.asyncio
async def test_revoke_one_propagates_unexpected_remote_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.hasn.service.ragflow_provisioning_service as provisioning_mod

    from backend.app.hasn.service.ragflow_provisioning_service import RAGFlowProvisioningService

    client = FailingDeleteRAGFlowClient('https://knowledge.example', status_code=500)
    monkeypatch.setattr(provisioning_mod, 'RAGFlowClient', lambda _base_url: client)
    credential = SimpleNamespace(
        id=13,
        status='active',
        api_key='still-live-token',
        instance=SimpleNamespace(endpoint='https://knowledge.example'),
    )
    repository = InMemoryCredentialRepository(instance=credential.instance, credential=credential)

    with pytest.raises(httpx.HTTPStatusError):
        await RAGFlowProvisioningService(repository).revoke_one(credential_id=13)

    assert repository.revoked == []


def test_rsa_encrypt_password_uses_ragflow_public_key_contract() -> None:
    from backend.app.hasn.util.rsa_pwd import rsa_encrypt_password

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    encrypted = rsa_encrypt_password('plain-password', public_pem.decode('utf-8'))
    decrypted = private_key.decrypt(base64.b64decode(encrypted), padding.PKCS1v15())

    assert base64.b64decode(decrypted).decode('utf-8') == 'plain-password'


def test_secret_crypto_handles_empty_and_legacy_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.hasn.util.secret_crypto as secret_crypto

    assert secret_crypto.encrypt_ragflow_secret('') == b''
    assert not secret_crypto.decrypt_ragflow_secret(None)
    assert not secret_crypto.decrypt_ragflow_secret(b'')

    def raise_for_legacy(_encoded: str) -> str:
        raise ValueError('not encrypted')

    monkeypatch.setattr(secret_crypto.key_encryption, 'decrypt', raise_for_legacy)

    assert secret_crypto.decrypt_ragflow_secret(b'legacy-token') == 'legacy-token'


def test_credential_payload_ships_plaintext_key_for_active_only() -> None:
    """daemon-direct（设计 §2.3/§9.1）：active 凭据下发明文 api_key 供 daemon 建直连适配器；
    非 active 一律不下发；脱敏占位 api_key_encrypted 仍只回 'stored'（不泄密文）。"""
    from backend.app.hasn.service.workbench_domain_service import _credential_payload
    from backend.app.llm.core.encryption import key_encryption

    ciphertext = key_encryption.encrypt('ragflow-tenant-key')

    def credential(status: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=1,
            user_id=7,
            app_instance_id=3,
            credential_ref=ciphertext,
            status=status,
            last_error=None,
            config={'ragflow_user_id': 'rf-user', 'ragflow_tenant_id': 'rf-tenant'},
        )

    active = _credential_payload(credential('active'))
    assert active['api_key'] == 'ragflow-tenant-key'
    assert active['api_key_encrypted'] == 'stored'  # 占位不泄密文
    assert active['instance_id'] == 3  # 输出字段名保持 instance_id（daemon 契约），底层 app_instance_id
    assert active['ragflow_user_id'] == 'rf-user'

    for inactive_status in ('pending', 'failed', 'revoked'):
        payload = _credential_payload(credential(inactive_status))
        assert payload['api_key'] is None, inactive_status
        assert payload['api_key_encrypted'] == 'stored'  # 有密文但不下发明文


def test_knowledge_write_scopes_registered_with_metadata() -> None:
    """RF-CLOUD：knowledge:upload/write/grant 三个新 scope 进 catalog（设计 §4.6）。"""
    from backend.app.mcp.scopes import scope_meta

    for key, expected_risk in (('knowledge:upload', 'medium'), ('knowledge:write', 'medium'), ('knowledge:grant', 'high')):
        meta = scope_meta(key)
        assert meta['domain'] == 'knowledge', key
        assert meta['risk'] == expected_risk, key
        assert meta['label'] and meta['label'] != key, key  # 有中文 label，未回退到裸 key
        assert meta['description'], key
