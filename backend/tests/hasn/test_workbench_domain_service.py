from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.common.exception import errors

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class _Base(DeclarativeBase):
    pass


class EnterpriseStub(_Base):
    __tablename__ = 'hasn_enterprise'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sa.String(128), default='')
    slug: Mapped[str] = mapped_column(sa.String(64), default='', unique=True)
    logo: Mapped[str | None] = mapped_column(sa.String(512), default=None)
    industry: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    company_size: Mapped[str | None] = mapped_column(sa.String(32), default=None)
    description: Mapped[str | None] = mapped_column(sa.Text, default=None)
    owner_user_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    join_policy: Mapped[str] = mapped_column(sa.String(16), default='invite_only')
    status: Mapped[str] = mapped_column(sa.String(16), default='active')


class MembershipStub(_Base):
    __tablename__ = 'hasn_enterprise_membership'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    user_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    role: Mapped[str] = mapped_column(sa.String(16), default='member')
    status: Mapped[str] = mapped_column(sa.String(16), default='pending')
    apply_message: Mapped[str | None] = mapped_column(sa.Text, default=None)
    apply_via: Mapped[str | None] = mapped_column(sa.String(16), default=None)
    invite_code: Mapped[str | None] = mapped_column(sa.String(32), default=None)
    decided_by: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    decided_at: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    decision_note: Mapped[str | None] = mapped_column(sa.Text, default=None)


class UserStub(_Base):
    __tablename__ = 'sys_user'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(sa.String(64), default='')
    nickname: Mapped[str] = mapped_column(sa.String(64), default='')
    phone: Mapped[str | None] = mapped_column(sa.String(11), default=None)


class InviteCodeStub(_Base):
    __tablename__ = 'hasn_enterprise_invite_code'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    code: Mapped[str] = mapped_column(sa.String(32), default='', unique=True)
    created_by: Mapped[int] = mapped_column(sa.Integer, default=0)
    max_uses: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    used_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    expires_at: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    auto_approve: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class ActiveWorkspaceStub(_Base):
    __tablename__ = 'hasn_user_active_workspace'

    user_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(sa.String(16), default='personal')
    enterprise_id: Mapped[int | None] = mapped_column(sa.Integer, default=None)


class WorkspaceAppStub(_Base):
    __tablename__ = 'hasn_workspace_app'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    workspace_kind: Mapped[str] = mapped_column(sa.String(16), default='personal')
    user_id: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='')
    status: Mapped[str] = mapped_column(sa.String(16), default='active')
    config: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    enabled_by: Mapped[int | None] = mapped_column(sa.Integer, default=None)


# 实施 03 收编后：知识库实例/凭据底层是统一应用平台底座 hasn_app_instance/hasn_app_credential
# （app_id='knowledge'，RAGFlow 私有字段在 config，密文在 credential_ref 字符串）。
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
    # 生产模型按 created_time 排序选当前实例；测试桩需镜像该列，
    # 否则 _knowledge_instance_for_workspace 的 order_by(created_time) 取属性即 AttributeError。
    created_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    updated_time: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True, default=None)


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


class _IdentityKeyEncryption:
    """测试用：identity 加解密，避免桩里塞真实 Fernet token（收编后服务用 key_encryption）。"""

    @staticmethod
    def encrypt(plaintext: str) -> str:
        return plaintext

    @staticmethod
    def decrypt(ciphertext: str) -> str:
        return ciphertext


class CapturingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event_name: str, payload: dict) -> None:
        self.events.append((event_name, payload))


class CapturingProvisioningService:
    def __init__(self) -> None:
        self.provisioned: list[tuple[int, int]] = []

    async def provision_one(self, user_id: int, instance_id: int) -> None:
        self.provisioned.append((user_id, instance_id))
        return




# RF-CLOUD：删除数据面单测后，RagFlow 数据面测试桩（CapturingRAGFlowClient /
# ExpiringRAGFlowClient / RefreshingProvisioningService / _ragflow_service /
# _seed_active_ragflow_workspace）已随之移除——云端不再直连 RagFlow 数据面。


@pytest_asyncio.fixture
async def db_session(monkeypatch) -> AsyncGenerator[AsyncSession, None]:
    import backend.app.hasn.service.workbench_domain_service as service_mod

    replacements = {
        'HasnEnterprise': EnterpriseStub,
        'HasnEnterpriseMembership': MembershipStub,
        'HasnEnterpriseInviteCode': InviteCodeStub,
        'HasnUserActiveWorkspace': ActiveWorkspaceStub,
        'HasnWorkspaceApp': WorkspaceAppStub,
        'HasnAppInstance': RagflowInstanceStub,
        'HasnAppCredential': RagflowCredentialStub,
        'User': UserStub,
    }
    for name, replacement in replacements.items():
        monkeypatch.setattr(service_mod, name, replacement, raising=True)

    monkeypatch.setattr(service_mod, 'key_encryption', _IdentityKeyEncryption(), raising=True)

    engine = create_async_engine('sqlite+aiosqlite:///:memory:', future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _service(enterprise_bus: CapturingBus | None = None, workbench_bus: CapturingBus | None = None):
    from backend.app.hasn.service.workbench_domain_service import WorkbenchDomainService

    return WorkbenchDomainService(
        enterprise_bus=enterprise_bus or CapturingBus(),
        workbench_bus=workbench_bus or CapturingBus(),
    )




@pytest.mark.asyncio
async def test_create_enterprise_approves_owner_and_installs_knowledge(db_session: AsyncSession) -> None:
    bus = CapturingBus()
    service = _service(enterprise_bus=bus)

    enterprise = await service.create_enterprise(
        db_session,
        user_id=11,
        name='Acme',
        description='Ops',
        logo='https://cdn.example.com/assets/enterprise-logos/acme.png',
        industry='software',
        company_size='11-50',
        join_policy='invite_only',
    )

    assert enterprise['id'] == 1
    assert enterprise['slug'] == 'acme'
    assert enterprise['logo'] == 'https://cdn.example.com/assets/enterprise-logos/acme.png'
    assert enterprise['industry'] == 'software'
    assert enterprise['company_size'] == '11-50'
    assert bus.events == [('on_enterprise_created', {'enterprise_id': 1, 'owner_user_id': 11})]

    owner = (await db_session.execute(sa.select(MembershipStub).where(MembershipStub.enterprise_id == 1))).scalar_one()
    assert (owner.user_id, owner.role, owner.status) == (11, 'owner', 'approved')

    app = (
        await db_session.execute(sa.select(WorkspaceAppStub).where(WorkspaceAppStub.enterprise_id == 1))
    ).scalar_one()
    assert (app.workspace_kind, app.app_id, app.status, app.enabled_by) == ('enterprise', 'knowledge', 'active', 11)


@pytest.mark.asyncio
async def test_create_enterprise_generates_slug_from_name_without_common_suffix(
    db_session: AsyncSession,
) -> None:
    service = _service()

    first = await service.create_enterprise(db_session, user_id=11, name='未来科技有限责任公司')
    second = await service.create_enterprise(db_session, user_id=12, name='未来科技')

    assert first['slug'] == 'wlkj'
    assert second['slug'] == 'wlkj-2'


@pytest.mark.asyncio
async def test_create_enterprise_keeps_optional_profile_fields_empty_when_omitted(
    db_session: AsyncSession,
) -> None:
    service = _service()

    enterprise = await service.create_enterprise(db_session, user_id=11, name='星河科技有限公司')

    assert enterprise['slug'] == 'xhkj'
    assert enterprise['logo'] is None
    assert enterprise['industry'] is None
    assert enterprise['company_size'] is None


@pytest.mark.asyncio
async def test_invite_auto_approve_switches_active_workspace_and_invalidates_used_code(
    db_session: AsyncSession,
) -> None:
    bus = CapturingBus()
    service = _service(enterprise_bus=bus)
    enterprise = await service.create_enterprise(
        db_session,
        user_id=11,
        name='Acme',
    )
    invite = await service.create_invite_code(
        db_session,
        enterprise_id=enterprise['id'],
        created_by=11,
        code='JOIN-1',
        max_uses=1,
        auto_approve=True,
    )

    application = await service.apply_enterprise(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=12,
        apply_message='let me in',
        invite_code=invite['code'],
    )
    active = await service.switch_active_workspace(
        db_session,
        user_id=12,
        kind='enterprise',
        enterprise_id=enterprise['id'],
    )
    workspaces = await service.list_user_workspaces(db_session, user_id=12)

    assert application['status'] == 'approved'
    assert active['kind'] == 'enterprise'
    assert active['enterprise_id'] == enterprise['id']
    assert [workspace['kind'] for workspace in workspaces['available']] == ['personal', 'enterprise']
    assert ('on_member_approved', {'enterprise_id': enterprise['id'], 'user_id': 12}) in bus.events
    assert (
        'on_workspace_switched',
        {
            'user_id': 12,
            'prev_workspace': {'kind': 'personal', 'enterprise_id': None},
            'next_workspace': {'kind': 'enterprise', 'enterprise_id': enterprise['id']},
        },
    ) in bus.events

    with pytest.raises(errors.RequestError, match='invite_code_used_up'):
        await service.apply_enterprise(
            db_session,
            enterprise_id=enterprise['id'],
            user_id=13,
            apply_message=None,
            invite_code=invite['code'],
        )


@pytest.mark.asyncio
async def test_list_user_workspaces_returns_cloud_aggregated_workspace_stats(
    db_session: AsyncSession,
) -> None:
    service = _service()
    enterprise = await service.create_enterprise(
        db_session,
        user_id=11,
        name='Acme',
        description='Ops workspace',
        logo='https://cdn.example.com/assets/enterprise-logos/acme.png',
        industry='software',
        company_size='11-50',
    )
    db_session.add_all([
        MembershipStub(enterprise_id=enterprise['id'], user_id=12, role='admin', status='approved'),
        MembershipStub(enterprise_id=enterprise['id'], user_id=13, role='member', status='approved'),
        MembershipStub(enterprise_id=enterprise['id'], user_id=14, role='admin', status='pending'),
        WorkspaceAppStub(
            workspace_kind='enterprise',
            enterprise_id=enterprise['id'],
            app_id='disabled-app',
            status='disabled',
        ),
        WorkspaceAppStub(
            workspace_kind='personal',
            user_id=12,
            app_id='personal-tool',
            status='active',
        ),
    ])
    await db_session.flush()

    workspaces = await service.list_user_workspaces(db_session, user_id=12)

    personal = workspaces['available'][0]
    enterprise_workspace = workspaces['available'][1]
    assert personal['member_count'] == 1
    assert personal['app_count'] == 1
    assert personal['admin_count'] == 1
    assert enterprise_workspace['description'] == 'Ops workspace'
    assert enterprise_workspace['logo'] == 'https://cdn.example.com/assets/enterprise-logos/acme.png'
    assert enterprise_workspace['industry'] == 'software'
    assert enterprise_workspace['company_size'] == '11-50'
    assert enterprise_workspace['member_count'] == 3
    assert enterprise_workspace['app_count'] == 1
    assert enterprise_workspace['admin_count'] == 2


@pytest.mark.asyncio
async def test_list_members_returns_cloud_user_profile_fields(db_session: AsyncSession) -> None:
    service = _service()
    enterprise = await service.create_enterprise(db_session, user_id=11, name='Acme')
    db_session.add_all([
        UserStub(id=11, username='owner', nickname='阿月', phone='13800138000'),
        UserStub(id=12, username='member', nickname='小林', phone='13900139000'),
        MembershipStub(enterprise_id=enterprise['id'], user_id=12, role='member', status='approved'),
    ])
    await db_session.flush()

    members = await service.list_members(db_session, enterprise_id=enterprise['id'])

    by_user_id = {item['user_id']: item for item in members['items']}
    assert by_user_id[11]['nickname'] == '阿月'
    assert by_user_id[11]['phone'] == '13800138000'
    assert by_user_id[12]['nickname'] == '小林'
    assert by_user_id[12]['phone'] == '13900139000'


@pytest.mark.asyncio
async def test_invite_codes_reject_revoked_and_expired_codes(db_session: AsyncSession) -> None:
    service = _service()
    enterprise = await service.create_enterprise(
        db_session,
        user_id=11,
        name='Acme',
    )
    revoked = await service.create_invite_code(
        db_session,
        enterprise_id=enterprise['id'],
        created_by=11,
        code='REVOKED',
        auto_approve=True,
    )
    await service.revoke_invite_code(
        db_session,
        enterprise_id=enterprise['id'],
        code=revoked['code'],
    )
    expired = await service.create_invite_code(
        db_session,
        enterprise_id=enterprise['id'],
        created_by=11,
        code='EXPIRED',
        expires_at=service_mod_timezone_now_minus_one_day(),
        auto_approve=True,
    )

    with pytest.raises(errors.RequestError, match='invite_code_revoked'):
        await service.apply_enterprise(
            db_session,
            enterprise_id=enterprise['id'],
            user_id=12,
            apply_message=None,
            invite_code=revoked['code'],
        )
    with pytest.raises(errors.RequestError, match='invite_code_expired'):
        await service.apply_enterprise(
            db_session,
            enterprise_id=enterprise['id'],
            user_id=13,
            apply_message=None,
            invite_code=expired['code'],
        )


@pytest.mark.asyncio
async def test_remove_current_enterprise_member_falls_back_to_personal_and_emits_hooks(
    db_session: AsyncSession,
) -> None:
    bus = CapturingBus()
    service = _service(enterprise_bus=bus)
    enterprise = await service.create_enterprise(
        db_session,
        user_id=11,
        name='Acme',
    )
    invite = await service.create_invite_code(
        db_session,
        enterprise_id=enterprise['id'],
        created_by=11,
        code='JOIN-2',
        auto_approve=True,
    )
    await service.apply_enterprise(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=12,
        apply_message=None,
        invite_code=invite['code'],
    )
    await service.switch_active_workspace(
        db_session,
        user_id=12,
        kind='enterprise',
        enterprise_id=enterprise['id'],
    )

    await service.remove_member(db_session, enterprise_id=enterprise['id'], user_id=12)

    active = await service.get_active_workspace(db_session, user_id=12)
    membership = (
        await db_session.execute(
            sa.select(MembershipStub).where(
                MembershipStub.enterprise_id == enterprise['id'],
                MembershipStub.user_id == 12,
            )
        )
    ).scalar_one()
    assert active == {'kind': 'personal', 'enterprise_id': None}
    assert membership.status == 'left'
    assert (
        'on_workspace_switched',
        {
            'user_id': 12,
            'prev_workspace': {'kind': 'enterprise', 'enterprise_id': enterprise['id']},
            'next_workspace': {'kind': 'personal', 'enterprise_id': None},
        },
    ) in bus.events
    assert ('on_member_left', {'enterprise_id': enterprise['id'], 'user_id': 12}) in bus.events


@pytest.mark.asyncio
async def test_delete_enterprise_falls_back_all_active_members_and_emits_disband_hook(
    db_session: AsyncSession,
) -> None:
    bus = CapturingBus()
    service = _service(enterprise_bus=bus)
    enterprise = await service.create_enterprise(
        db_session,
        user_id=11,
        name='Acme',
    )
    for user_id in (12, 13):
        invite = await service.create_invite_code(
            db_session,
            enterprise_id=enterprise['id'],
            created_by=11,
            code=f'JOIN-{user_id}',
            auto_approve=True,
        )
        await service.apply_enterprise(
            db_session,
            enterprise_id=enterprise['id'],
            user_id=user_id,
            apply_message=None,
            invite_code=invite['code'],
        )
        await service.switch_active_workspace(
            db_session,
            user_id=user_id,
            kind='enterprise',
            enterprise_id=enterprise['id'],
        )

    await service.delete_enterprise(db_session, enterprise_id=enterprise['id'])

    assert await service.get_active_workspace(db_session, user_id=11) == {
        'kind': 'personal',
        'enterprise_id': None,
    }
    assert await service.get_active_workspace(db_session, user_id=12) == {
        'kind': 'personal',
        'enterprise_id': None,
    }
    assert await service.get_active_workspace(db_session, user_id=13) == {
        'kind': 'personal',
        'enterprise_id': None,
    }
    enterprise_row = (
        await db_session.execute(sa.select(EnterpriseStub).where(EnterpriseStub.id == enterprise['id']))
    ).scalar_one()
    assert enterprise_row.status == 'deleted'
    assert (
        'on_enterprise_disbanded',
        {'enterprise_id': enterprise['id'], 'member_user_ids': [11, 12, 13]},
    ) in bus.events


@pytest.mark.asyncio
async def test_enterprise_workbench_app_enable_disable_uses_current_workspace_and_hooks(
    db_session: AsyncSession,
) -> None:
    workbench_bus = CapturingBus()
    service = _service(workbench_bus=workbench_bus)
    enterprise = await service.create_enterprise(db_session, user_id=21, name='Acme')
    await service.switch_active_workspace(
        db_session,
        user_id=21,
        kind='enterprise',
        enterprise_id=enterprise['id'],
    )
    disabled = await service.disable_current_workspace_app(db_session, user_id=21, app_id='knowledge')
    enabled = await service.enable_current_workspace_app(db_session, user_id=21, app_id='knowledge')

    assert disabled['status'] == 'disabled'
    assert enabled['status'] == 'active'
    assert workbench_bus.events == [
        (
            'on_app_disabled',
            {'workspace_kind': 'enterprise', 'user_id': None, 'enterprise_id': enterprise['id'], 'app_id': 'knowledge'},
        ),
        (
            'on_app_enabled',
            {'workspace_kind': 'enterprise', 'user_id': None, 'enterprise_id': enterprise['id'], 'app_id': 'knowledge'},
        ),
    ]


@pytest.mark.asyncio
async def test_workbench_rejects_unknown_apps_and_protects_auto_installed_personal_apps(
    db_session: AsyncSession,
) -> None:
    service = _service()
    await service.ensure_auto_apps(
        db_session,
        workspace_kind='personal',
        user_id=21,
        enterprise_id=None,
        enabled_by=21,
    )

    with pytest.raises(errors.NotFoundError):
        await service.enable_current_workspace_app(db_session, user_id=21, app_id='missing-app')
    with pytest.raises(errors.RequestError, match='auto_installed_personal_app_cannot_be_disabled'):
        await service.disable_current_workspace_app(db_session, user_id=21, app_id='knowledge')


@pytest.mark.asyncio
async def test_current_knowledge_credentials_follow_active_workspace(db_session: AsyncSession) -> None:
    service = _service()
    enterprise = await service.create_enterprise(db_session, user_id=31, name='Acme')

    public_instance = RagflowInstanceStub(
        app_id='knowledge',
        scope='public',
        enterprise_id=None,
        endpoint='https://knowledge.example',
        credential_ref='admin-public',
        config={'public_pem': 'pem'},
        status='active',
    )
    enterprise_instance = RagflowInstanceStub(
        app_id='knowledge',
        scope='enterprise',
        enterprise_id=enterprise['id'],
        endpoint='https://enterprise.example',
        credential_ref='admin-enterprise',
        config={'public_pem': 'pem'},
        status='active',
    )
    db_session.add_all([public_instance, enterprise_instance])
    await db_session.flush()
    db_session.add_all([
        RagflowCredentialStub(
            app_id='knowledge',
            user_id=32,
            app_instance_id=public_instance.id,
            credential_ref='key-public',
            config={'ragflow_user_id': 'u-public', 'ragflow_tenant_id': 't-public'},
            status='active',
        ),
        RagflowCredentialStub(
            app_id='knowledge',
            user_id=32,
            app_instance_id=enterprise_instance.id,
            credential_ref='key-enterprise',
            config={'ragflow_user_id': 'u-enterprise', 'ragflow_tenant_id': 't-enterprise'},
            status='active',
        ),
    ])
    await db_session.flush()

    personal = await service.get_current_knowledge_credentials(db_session, user_id=32)
    await service.apply_enterprise(
        db_session, enterprise_id=enterprise['id'], user_id=32, apply_message=None, invite_code=None
    )
    await service.approve_application(db_session, enterprise_id=enterprise['id'], app_id=2, decided_by=31)
    await service.switch_active_workspace(db_session, user_id=32, kind='enterprise', enterprise_id=enterprise['id'])
    enterprise_credentials = await service.get_current_knowledge_credentials(db_session, user_id=32)

    assert personal['workspace'] == {'kind': 'personal', 'enterprise_id': None}
    assert personal['credential']['ragflow_user_id'] == 'u-public'
    assert enterprise_credentials['workspace'] == {'kind': 'enterprise', 'enterprise_id': enterprise['id']}
    assert enterprise_credentials['credential']['ragflow_user_id'] == 'u-enterprise'


@pytest.mark.asyncio
async def test_refresh_current_knowledge_credentials_triggers_provision_for_active_instance(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    import backend.app.hasn.service.workbench_domain_service as service_mod

    service = _service()
    provisioner = CapturingProvisioningService()
    monkeypatch.setattr(service_mod, 'ragflow_provisioning_service', provisioner, raising=False)
    public_instance = RagflowInstanceStub(
        app_id='knowledge',
        scope='public',
        enterprise_id=None,
        endpoint='https://knowledge.example',
        credential_ref='admin-public',
        config={'public_pem': 'pem'},
        status='active',
    )
    db_session.add(public_instance)
    await db_session.flush()

    refreshed = await service.refresh_current_knowledge_credentials(db_session, user_id=32)

    assert provisioner.provisioned == [(32, public_instance.id)]
    assert refreshed['workspace'] == {'kind': 'personal', 'enterprise_id': None}
    assert refreshed['status'] == 'pending'
    assert refreshed['credential'] is None


@pytest.mark.asyncio
async def test_enterprise_ragflow_instance_config_upserts_and_disables(db_session: AsyncSession) -> None:
    service = _service()
    enterprise = await service.create_enterprise(db_session, user_id=41, name='Acme')

    saved = await service.save_enterprise_ragflow_instance(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=41,
        url='https://knowledge.example',
        admin_api_key='secret',
        public_pem='pem',
        default_embd_id='bge-m3',
        default_llm_id='qwen',
    )
    fetched = await service.get_enterprise_ragflow_instance(db_session, enterprise_id=enterprise['id'], user_id=41)
    disabled = await service.disable_enterprise_ragflow_instance(db_session, enterprise_id=enterprise['id'], user_id=41)

    assert saved['status'] == 'active'
    assert saved['admin_api_key_encrypted'] == 'stored'
    assert fetched['url'] == 'https://knowledge.example'
    assert disabled['status'] == 'disabled'


@pytest.mark.asyncio
async def test_enterprise_ragflow_instance_requires_owner_or_approved_admin(db_session: AsyncSession) -> None:
    service = _service()
    enterprise = await service.create_enterprise(db_session, user_id=51, name='Acme')
    db_session.add_all([
        MembershipStub(enterprise_id=enterprise['id'], user_id=52, role='admin', status='approved'),
        MembershipStub(enterprise_id=enterprise['id'], user_id=53, role='member', status='approved'),
        MembershipStub(enterprise_id=enterprise['id'], user_id=54, role='admin', status='pending'),
    ])
    await db_session.flush()

    await service.save_enterprise_ragflow_instance(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=51,
        url='https://knowledge.example',
        admin_api_key='owner-secret',
        public_pem='pem',
    )

    owner_fetched = await service.get_enterprise_ragflow_instance(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=51,
    )
    admin_tested = await service.test_enterprise_ragflow_instance(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=52,
    )
    admin_saved = await service.save_enterprise_ragflow_instance(
        db_session,
        enterprise_id=enterprise['id'],
        user_id=52,
        url='https://knowledge-admin.example',
        admin_api_key='admin-secret',
        public_pem='admin-pem',
    )

    assert owner_fetched['url'] == 'https://knowledge.example'
    assert admin_tested == {'enterprise_id': enterprise['id'], 'ok': True}
    assert admin_saved['url'] == 'https://knowledge-admin.example'

    for user_id in (53, 54, 55):
        with pytest.raises(errors.ForbiddenError):
            await service.get_enterprise_ragflow_instance(db_session, enterprise_id=enterprise['id'], user_id=user_id)
        with pytest.raises(errors.ForbiddenError):
            await service.save_enterprise_ragflow_instance(
                db_session,
                enterprise_id=enterprise['id'],
                user_id=user_id,
                url='https://blocked.example',
                admin_api_key='blocked',
                public_pem='blocked',
            )
        with pytest.raises(errors.ForbiddenError):
            await service.test_enterprise_ragflow_instance(db_session, enterprise_id=enterprise['id'], user_id=user_id)
        with pytest.raises(errors.ForbiddenError):
            await service.disable_enterprise_ragflow_instance(
                db_session,
                enterprise_id=enterprise['id'],
                user_id=user_id,
            )


# RF-CLOUD：云端数据面方法（list/search/upload current knowledge）已删除——其单测
# （datasets 加载 / search+upload 代理 / 无凭据拦截 / 401 刷新重试）随之移除。该数据面
# 现由 hasn-node daemon 经 KnowledgeAdapter 直连 RagFlow，覆盖在 hasn-node 的
# `workbench_knowledge_contract.rs`（进程内 MCP → KnowledgeBroker → stub RagFlow）。
# 云端只保留凭据下发 + 企业实例配置的单测（见上方 test_current_knowledge_credentials_* /
# test_enterprise_ragflow_instance_*）。


def service_mod_timezone_now_minus_one_day():
    from backend.utils.timezone import timezone

    return timezone.now() - timedelta(days=1)
