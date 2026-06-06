"""应用 → 实例 → 调用句柄 的统一解析（设计 14-AI-Native应用平台/11 §3）。

控制面唯一新增的通用中间件：把知识库现在硬编码做的"按 workspace 选 RAGFlow 实例"，
提升为对所有 AI-Native App 生效的一层。调用方（daemon / Agent / 前端）永远不自己拼实例
地址，统一向控制面 resolve() 拿 InstanceHandle —— 这保证"桌面端界面统一、后端连不同实例"
对调用方透明。

- Phase A（本次）：内置应用恒为 ``gateway_internal``（云端就地执行，无外部实例行），
  resolve() 只从 manifest 读 tool/ui 的 transport；非 internal transport 因尚无实例行而抛 15050。
- Phase B：``_pick_instance`` 读 ``hasn_app_instance``（企业 active → 企业实例；否则 / 回落 → 公共实例），
  ``_mint_credential`` 解密接入凭据供 cloud_relay 代签 / daemon_direct 下发。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model import HasnAppInstance
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# transport 四态（设计 11 §2.2）
TRANSPORT_GATEWAY_INTERNAL = 'gateway_internal'  # 云端就地执行（endpoint=云端自身）
TRANSPORT_CLOUD_RELAY = 'cloud_relay'  # 云端 HMAC 签名后转发到实例 endpoint，app secret 不下发
TRANSPORT_DAEMON_DIRECT = 'daemon_direct'  # daemon 拿短期凭据直连实例（人类 UI 默认）
TRANSPORT_FRONTEND_DIRECT = 'frontend_direct'  # 前端拿短期受限句柄直连（大文件 / 流式）

VALID_TRANSPORTS = frozenset(
    {
        TRANSPORT_GATEWAY_INTERNAL,
        TRANSPORT_CLOUD_RELAY,
        TRANSPORT_DAEMON_DIRECT,
        TRANSPORT_FRONTEND_DIRECT,
    }
)

# 调用面（设计 11 §3.1）：tool=Agent 工具面，ui=人类 UI 面，各取各自声明的 transport
FACE_TOOL = 'tool'
FACE_UI = 'ui'


class InstanceResolutionError(Exception):
    """实例解析失败，携带设计 11 §11 新增错误码。

    - 15050 实例未配置 / 不可达
    - 15051 实例接入凭据无效
    - 15052 该 transport 不允许此调用面（如 Tool 面请求 frontend_direct）
    """

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class InstanceHandle:
    """实例句柄（设计 11 §2.1）：告诉调用方这次连哪个 endpoint、用哪种 transport、带什么短期凭据。"""

    app_id: str
    instance_id: str | None
    scope: str  # public | enterprise
    endpoint: str | None  # gateway_internal 为 None（=云端自身）
    transport: str
    credential: dict[str, Any] | None  # 对前端 redact；cloud_relay 时只在云端使用
    expires_at: str | None

    @property
    def is_internal(self) -> bool:
        return self.transport == TRANSPORT_GATEWAY_INTERNAL


class InstanceResolver:
    """resolve(app_id, workspace, face) -> InstanceHandle（设计 11 §3.1）。"""

    async def resolve(
        self,
        db: AsyncSession,
        *,
        app_id: str,
        workspace: dict[str, Any],
        face: str,
        manifest: dict[str, Any] | None = None,
        tool_id: str | None = None,
    ) -> InstanceHandle:
        manifest = manifest or await ai_native_app_registry.get(db, app_id)
        transport = self._transport_for(manifest, face=face, tool_id=tool_id)
        if transport not in VALID_TRANSPORTS:
            raise InstanceResolutionError(code='15052', message=f'unknown_transport:{transport}')

        if transport == TRANSPORT_GATEWAY_INTERNAL:
            # 云端就地执行：endpoint = 云端自身，无外部实例 / 凭据（gateway_internal 是 cloud_relay 的特例）
            return InstanceHandle(
                app_id=app_id,
                instance_id=None,
                scope='public',
                endpoint=None,
                transport=transport,
                credential=None,
                expires_at=None,
            )

        instance = await self._pick_instance(db, app_id=app_id, workspace=workspace)
        if instance is None:
            raise InstanceResolutionError(code='15050', message='instance_not_configured')
        credential = await self._mint_credential(instance, transport=transport)
        return InstanceHandle(
            app_id=app_id,
            instance_id=str(instance.get('id')) if instance.get('id') is not None else None,
            scope=str(instance.get('scope') or 'public'),
            endpoint=instance.get('endpoint'),
            transport=transport,
            credential=credential,
            expires_at=None,
        )

    def _transport_for(self, manifest: dict[str, Any], *, face: str, tool_id: str | None) -> str:
        """从 manifest 读 transport：声明在「接口」粒度，不是应用粒度（设计 11 §2.3）。"""
        manifest_json = manifest.get('manifest_json') or manifest
        if face == FACE_TOOL:
            for tool in manifest_json.get('tools') or []:
                if tool_id is None or tool.get('tool_id') == tool_id:
                    return str(tool.get('transport') or TRANSPORT_GATEWAY_INTERNAL)
            return TRANSPORT_GATEWAY_INTERNAL
        # face == ui：从 ui_interfaces 取；默认 daemon_direct（凭据不进浏览器，设计 11 §0.3/§7.2）
        for iface in manifest_json.get('ui_interfaces') or []:
            return str(iface.get('transport') or TRANSPORT_DAEMON_DIRECT)
        return TRANSPORT_DAEMON_DIRECT

    async def _pick_instance(
        self, db: AsyncSession, *, app_id: str, workspace: dict[str, Any]
    ) -> dict[str, Any] | None:
        """选实例 + 回落（设计 11 §3.2）：企业自建且 active → 企业实例；否则 / 回落 → 公共实例。"""
        if workspace.get('kind') == 'enterprise' and workspace.get('enterprise_id') is not None:
            stmt = (
                sa.select(HasnAppInstance)
                .where(
                    HasnAppInstance.app_id == app_id,
                    HasnAppInstance.scope == 'enterprise',
                    HasnAppInstance.enterprise_id == int(workspace['enterprise_id']),
                )
                .order_by(HasnAppInstance.created_time.desc())
            )
            row = (await db.execute(stmt)).scalars().first()
            if row is not None and row.status == 'active':
                return self._instance_to_dict(row)
        # 默认 / 回落 → 公共实例（迁移后每 app_id 至多一条 public，partial unique 保证；
        # 仍补 ORDER BY created_time DESC 作防御，避免历史脏数据下取序不定）
        stmt = (
            sa.select(HasnAppInstance)
            .where(
                HasnAppInstance.app_id == app_id,
                HasnAppInstance.scope == 'public',
            )
            .order_by(HasnAppInstance.created_time.desc())
        )
        row = (await db.execute(stmt)).scalars().first()
        if row is not None and row.status == 'active':
            return self._instance_to_dict(row)
        return None

    def _instance_to_dict(self, row: HasnAppInstance) -> dict[str, Any]:
        return {
            'id': row.id,
            'app_id': row.app_id,
            'scope': row.scope,
            'enterprise_id': row.enterprise_id,
            'endpoint': row.endpoint,
            'transport_default': row.transport_default,
            'credential_ref': row.credential_ref,
            'status': row.status,
            'config': row.config,
        }

    async def _mint_credential(self, instance: dict[str, Any], *, transport: str) -> dict[str, Any] | None:
        """签发短期凭据 / 取接入凭据（设计 11 §3.1/§10）。

        - cloud_relay：解密登记的 app secret，仅在云端用于 HMAC 代签，绝不下发 daemon/前端。
        - daemon_direct / frontend_direct：短期 token（P4 性能档，Tool 面不走）。
        """
        if transport == TRANSPORT_CLOUD_RELAY:
            from backend.app.llm.core.encryption import key_encryption

            ref = instance.get('credential_ref')
            if not ref:
                return None
            try:
                secret = key_encryption.decrypt(ref)
            except Exception:
                # 解密失败 → 凭据无效（由 cloud_relay 侧落 15051）
                return {'type': 'app_secret', 'value': ''}
            return {'type': 'app_secret', 'value': secret}
        return None


instance_resolver: InstanceResolver = InstanceResolver()
