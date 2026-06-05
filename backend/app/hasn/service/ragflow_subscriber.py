from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import sqlalchemy as sa

from backend.app.hasn.model import HasnEnterpriseMembership, HasnHumans, HasnRagflowCredential, HasnRagflowInstance
from backend.app.hasn.service.enterprise_event_bus import EnterpriseEventBus, enterprise_event_bus
from backend.app.hasn.service.ragflow_provisioning_service import (
    RAGFlowProvisioningService,
    SqlAlchemyRAGFlowCredentialRepository,
)
from backend.app.hasn.service.ws_router import ws_router
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone


class RAGFlowActions(Protocol):
    async def create_placeholder(self, *, enterprise_id: int) -> None: ...
    async def provision_member(self, *, enterprise_id: int, user_id: int) -> None: ...
    async def revoke_member(self, *, enterprise_id: int, user_id: int) -> None: ...
    async def notify_credentials_changed(self, *, user_id: int) -> None: ...
    async def disable_enterprise_instance(self, *, enterprise_id: int, member_user_ids: list[int]) -> None: ...


@dataclass
class RecordingRAGFlowActions:
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def create_placeholder(self, *, enterprise_id: int) -> None:
        self.calls.append(('create_placeholder', {'enterprise_id': enterprise_id}))

    async def provision_member(self, *, enterprise_id: int, user_id: int) -> None:
        self.calls.append(('provision_member', {'enterprise_id': enterprise_id, 'user_id': user_id}))

    async def revoke_member(self, *, enterprise_id: int, user_id: int) -> None:
        self.calls.append(('revoke_member', {'enterprise_id': enterprise_id, 'user_id': user_id}))

    async def notify_credentials_changed(self, *, user_id: int) -> None:
        self.calls.append(('notify_credentials_changed', {'user_id': user_id}))

    async def disable_enterprise_instance(self, *, enterprise_id: int, member_user_ids: list[int]) -> None:
        self.calls.append((
            'disable_enterprise_instance',
            {'enterprise_id': enterprise_id, 'member_user_ids': member_user_ids},
        ))


class SqlAlchemyRAGFlowActions:
    def __init__(self, provisioning_service: RAGFlowProvisioningService | None = None) -> None:
        self.provisioning_service = provisioning_service or RAGFlowProvisioningService(
            SqlAlchemyRAGFlowCredentialRepository()
        )

    async def create_placeholder(self, *, enterprise_id: int) -> None:
        async with async_db_session.begin() as db:
            existing = (
                await db.execute(
                    sa.select(HasnRagflowInstance).where(
                        HasnRagflowInstance.scope == 'enterprise',
                        HasnRagflowInstance.enterprise_id == enterprise_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return
            db.add(
                HasnRagflowInstance(
                    scope='enterprise',
                    enterprise_id=enterprise_id,
                    url='',
                    admin_api_key_encrypted=b'',
                    public_pem='',
                    status='pending_config',
                )
            )

    async def provision_member(self, *, enterprise_id: int, user_id: int) -> None:
        async with async_db_session.begin() as db:
            instance = await _enterprise_instance(db, enterprise_id)
            if instance is None:
                instance = HasnRagflowInstance(
                    scope='enterprise',
                    enterprise_id=enterprise_id,
                    url='',
                    admin_api_key_encrypted=b'',
                    public_pem='',
                    status='pending_config',
                )
                db.add(instance)
                await db.flush()
            instance_id = instance.id
        try:
            await self.provisioning_service.provision_one(user_id, instance_id)
        except Exception:
            await self.provisioning_service.repository.upsert_pending_credential(
                user_id=user_id,
                instance_id=instance_id,
                reason='provisioning deferred for retry',
            )

    async def revoke_member(self, *, enterprise_id: int, user_id: int) -> None:
        credential_ids: list[int] = []
        async with async_db_session() as db:
            instance = await _enterprise_instance(db, enterprise_id)
            if instance is None:
                return
            credentials = (
                (
                    await db.execute(
                        sa.select(HasnRagflowCredential).where(
                            HasnRagflowCredential.user_id == user_id,
                            HasnRagflowCredential.instance_id == instance.id,
                            HasnRagflowCredential.status != 'revoked',
                        )
                    )
                )
                .scalars()
                .all()
            )
            credential_ids = [credential.id for credential in credentials]
        for credential_id in credential_ids:
            await self.provisioning_service.revoke_one(credential_id)
        # 撤销后必须推 WS，否则 daemon 的本地明文 key 不会被丢弃（设计 §2.3/§9.1）。
        if credential_ids:
            await self._notify_best_effort(user_id=user_id)

    async def _notify_best_effort(self, *, user_id: int) -> None:
        # 撤销在 DB 已生效（权威）；WS 通知仅是让 daemon 尽快丢弃本地明文 key 的提速手段，
        # 即便推送失败 daemon 也会在下次 refresh/401 时发现失效。故通知失败只记日志、绝不上抛，
        # 以免一个 WS/连接抖动把已完成的撤销流程炸断（设计 §2.3/§9.1）。
        try:
            await self.notify_credentials_changed(user_id=user_id)
        except Exception as exc:  # noqa: BLE001 — 通知是 best-effort，不能让它影响撤销
            log.warning(f'撤销后推送 KnowledgeCredentialsChanged 失败（user_id={user_id}）: {exc!r}')

    async def notify_credentials_changed(self, *, user_id: int) -> None:
        async with async_db_session() as db:
            human = (
                await db.execute(
                    sa.select(HasnHumans).where(
                        HasnHumans.user_id == user_id,
                        HasnHumans.status == 'active',
                    )
                )
            ).scalar_one_or_none()
        if human is None:
            return
        await ws_router.push_message_to(
            human.hasn_id,
            {
                'type': 'KnowledgeCredentialsChanged',
                'user_id': user_id,
                'created_time': timezone.now().isoformat(),
            },
        )

    async def disable_enterprise_instance(self, *, enterprise_id: int, member_user_ids: list[int]) -> None:
        credential_ids: list[int] = []
        async with async_db_session.begin() as db:
            instance = await _enterprise_instance(db, enterprise_id)
            if instance is None:
                return
            instance.status = 'disabled'
            credentials = (
                (
                    await db.execute(
                        sa.select(HasnRagflowCredential).where(
                            HasnRagflowCredential.instance_id == instance.id,
                            HasnRagflowCredential.status != 'revoked',
                        )
                    )
                )
                .scalars()
                .all()
            )
            credential_ids = [credential.id for credential in credentials]
        for credential_id in credential_ids:
            await self.provisioning_service.revoke_one(credential_id)
        # 实例禁用 = 全体成员凭据失效，逐个通知其 daemon 丢弃本地明文 key（设计 §2.3/§9.1）。
        for member_user_id in member_user_ids:
            await self._notify_best_effort(user_id=member_user_id)

    async def compensate_pending_credentials(self) -> int:
        to_provision: list[tuple[int, int]] = []
        async with async_db_session() as db:
            rows = (
                await db.execute(
                    sa
                    .select(HasnEnterpriseMembership, HasnRagflowInstance)
                    .join(
                        HasnRagflowInstance,
                        (HasnRagflowInstance.enterprise_id == HasnEnterpriseMembership.enterprise_id)
                        & (HasnRagflowInstance.scope == 'enterprise'),
                    )
                    .where(
                        HasnEnterpriseMembership.status == 'approved',
                        HasnRagflowInstance.status.in_(('pending_config', 'active')),
                    )
                )
            ).all()
            for membership, instance in rows:
                credential = (
                    await db.execute(
                        sa.select(HasnRagflowCredential).where(
                            HasnRagflowCredential.user_id == membership.user_id,
                            HasnRagflowCredential.instance_id == instance.id,
                        )
                    )
                ).scalar_one_or_none()
                if credential is not None and credential.status == 'active':
                    continue
                to_provision.append((membership.user_id, instance.id))
        processed = 0
        for user_id, instance_id in to_provision:
            await self.provisioning_service.provision_one(user_id, instance_id)
            processed += 1
        return processed


class RAGFlowSubscriber:
    def __init__(self, actions: RAGFlowActions | None = None) -> None:
        self.actions = actions or SqlAlchemyRAGFlowActions()

    async def on_enterprise_created(self, payload: dict) -> None:
        await self.actions.create_placeholder(enterprise_id=int(payload['enterprise_id']))

    async def on_member_approved(self, payload: dict) -> None:
        await self.actions.provision_member(
            enterprise_id=int(payload['enterprise_id']),
            user_id=int(payload['user_id']),
        )

    async def on_member_left(self, payload: dict) -> None:
        await self.actions.revoke_member(
            enterprise_id=int(payload['enterprise_id']),
            user_id=int(payload['user_id']),
        )

    async def on_workspace_switched(self, payload: dict) -> None:
        await self.actions.notify_credentials_changed(user_id=int(payload['user_id']))

    async def on_enterprise_disbanded(self, payload: dict) -> None:
        await self.actions.disable_enterprise_instance(
            enterprise_id=int(payload['enterprise_id']),
            member_user_ids=[int(user_id) for user_id in payload.get('member_user_ids', [])],
        )

    def register(self, bus: EnterpriseEventBus = enterprise_event_bus) -> None:
        bus.subscribe('on_enterprise_created', self.on_enterprise_created)
        bus.subscribe('on_member_approved', self.on_member_approved)
        bus.subscribe('on_member_left', self.on_member_left)
        bus.subscribe('on_workspace_switched', self.on_workspace_switched)
        bus.subscribe('on_enterprise_disbanded', self.on_enterprise_disbanded)


ragflow_subscriber = RAGFlowSubscriber()
ragflow_subscriber.register()


async def _enterprise_instance(db, enterprise_id: int):
    return (
        await db.execute(
            sa.select(HasnRagflowInstance).where(
                HasnRagflowInstance.scope == 'enterprise',
                HasnRagflowInstance.enterprise_id == enterprise_id,
            )
        )
    ).scalar_one_or_none()
