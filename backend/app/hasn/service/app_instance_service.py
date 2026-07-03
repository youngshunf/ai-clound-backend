"""AI-Native 应用实例 / 第三方注册服务（设计 14-AI-Native应用平台/11 §5）。

- 第三方注册即接入：绑定 developer_id↔app_id 所有权 + 建公共实例 + 发布 manifest（发布即用）。
- 企业自建实例：upsert scope=enterprise 实例，override 公共；其它空间仍回落公共。
- 补当前缺失的注册安全（§5.2）：所有权校验——仅 publisher 可改自己的 App。

凭据加密复用 key_encryption（与知识库 RAGFlow admin key 同源），credential_ref 存密文不存明文（§10）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model import HasnAiNativeAppManifest, HasnAppInstance, HasnAppPublisher
from backend.app.hasn.service import ai_native_app_registry as _registry_mod
from backend.common.exception import errors
from backend.common.security.encryption import key_encryption
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AppInstanceService:
    async def register_app(
        self,
        db: AsyncSession,
        *,
        developer_id: str,
        app_id: str,
        manifest_json: dict[str, Any],
        endpoint: str,
        credential: str,
        publisher_type: str = 'third_party',
    ) -> dict[str, Any]:
        """第三方注册：所有权绑定 + 建/更新公共实例 + 发布 manifest（发布即全网可用）。"""
        if not app_id:
            raise errors.RequestError(msg='app_id_required')
        if not endpoint or not endpoint.startswith('https://'):
            # 设计 11 §10：第三方 endpoint 强制 HTTPS
            raise errors.RequestError(msg='endpoint_must_be_https')

        await self._assert_ownership(db, app_id=app_id, developer_id=developer_id)
        await self._upsert_publisher(db, app_id=app_id, developer_id=developer_id, publisher_type=publisher_type)
        instance = await self._upsert_instance(
            db, app_id=app_id, scope='public', enterprise_id=None,
            endpoint=endpoint, credential=credential, transport_default='cloud_relay', config={},
        )
        manifest = await self._publish_third_party_manifest(db, app_id=app_id, manifest_json=manifest_json)
        return {
            'app_id': app_id,
            'developer_id': developer_id,
            'instance_id': instance.id,
            'manifest_status': manifest['status'],
            'status': 'published',
        }

    async def configure_enterprise_instance(
        self,
        db: AsyncSession,
        *,
        app_id: str,
        enterprise_id: int,
        endpoint: str,
        credential: str,
        config: dict[str, Any] | None = None,
        transport_default: str = 'cloud_relay',
    ) -> dict[str, Any]:
        """企业自建实例：override 公共；该企业 workspace 下 resolve() 自动解析到企业实例（设计 11 §5.3）。"""
        if not endpoint or not endpoint.startswith('https://'):
            raise errors.RequestError(msg='endpoint_must_be_https')
        instance = await self._upsert_instance(
            db, app_id=app_id, scope='enterprise', enterprise_id=int(enterprise_id),
            endpoint=endpoint, credential=credential, transport_default=transport_default, config=config or {},
        )
        return {'app_id': app_id, 'enterprise_id': int(enterprise_id), 'instance_id': instance.id, 'status': 'active'}

    async def _assert_ownership(self, db: AsyncSession, *, app_id: str, developer_id: str) -> None:
        """仅 publisher 可改自己的 App（设计 11 §5.2）。无记录=首次注册放行。"""
        row = (
            await db.execute(sa.select(HasnAppPublisher).where(HasnAppPublisher.app_id == app_id))
        ).scalars().first()
        if row is not None and row.developer_id != developer_id:
            raise errors.ForbiddenError(msg='app_ownership_conflict')

    async def _upsert_publisher(
        self, db: AsyncSession, *, app_id: str, developer_id: str, publisher_type: str
    ) -> HasnAppPublisher:
        row = (
            await db.execute(sa.select(HasnAppPublisher).where(HasnAppPublisher.app_id == app_id))
        ).scalars().first()
        if row is None:
            row = HasnAppPublisher(
                app_id=app_id, developer_id=developer_id, publisher_type=publisher_type, status='active'
            )
            db.add(row)
        else:
            row.developer_id = developer_id
            row.publisher_type = publisher_type
            row.status = 'active'
        await db.flush()
        return row

    async def _upsert_instance(
        self,
        db: AsyncSession,
        *,
        app_id: str,
        scope: str,
        enterprise_id: int | None,
        endpoint: str,
        credential: str,
        transport_default: str,
        config: dict[str, Any],
    ) -> HasnAppInstance:
        credential_ref = key_encryption.encrypt(credential) if credential else None
        stmt = sa.select(HasnAppInstance).where(
            HasnAppInstance.app_id == app_id, HasnAppInstance.scope == scope
        )
        if scope == 'enterprise':
            stmt = stmt.where(HasnAppInstance.enterprise_id == enterprise_id)
        row = (await db.execute(stmt)).scalars().first()
        if row is None:
            row = HasnAppInstance(
                app_id=app_id, scope=scope, enterprise_id=enterprise_id, endpoint=endpoint,
                transport_default=transport_default, credential_ref=credential_ref, status='active', config=config,
            )
            db.add(row)
        else:
            row.endpoint = endpoint
            row.transport_default = transport_default
            if credential_ref is not None:
                row.credential_ref = credential_ref
            row.status = 'active'
            row.config = config
        await db.flush()
        await db.refresh(row)
        return row

    async def _publish_third_party_manifest(
        self, db: AsyncSession, *, app_id: str, manifest_json: dict[str, Any]
    ) -> dict[str, Any]:
        """落库第三方 manifest 为 published（发布即用）。已存在则就地刷新。"""
        manifest_json = dict(manifest_json or {})
        manifest_json.setdefault('app_id', app_id)
        version = str(manifest_json.get('version') or '1.0.0')
        manifest_hash = _registry_mod._manifest_hash(manifest_json)
        row = (
            await db.execute(
                sa.select(HasnAiNativeAppManifest).where(
                    HasnAiNativeAppManifest.app_id == app_id,
                    HasnAiNativeAppManifest.status == 'published',
                )
            )
        ).scalars().first()
        if row is None:
            row = HasnAiNativeAppManifest(
                app_id=app_id, version=version, status='published',
                workspace_scope=list(manifest_json.get('workspace_scope') or ['personal']),
                collaboration_mode=str(manifest_json.get('collaboration_mode') or 'none'),
                manifest_json=manifest_json, manifest_hash=manifest_hash, published_at=timezone.now(),
            )
            db.add(row)
        else:
            row.version = version
            row.manifest_json = manifest_json
            row.manifest_hash = manifest_hash
            row.published_at = timezone.now()
        await db.flush()
        return {'app_id': app_id, 'status': 'published', 'manifest_hash': manifest_hash}


app_instance_service: AppInstanceService = AppInstanceService()
