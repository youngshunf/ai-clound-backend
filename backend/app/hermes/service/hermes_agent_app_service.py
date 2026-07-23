from __future__ import annotations

import secrets

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.model.user import User
from backend.app.hermes.model import (
    HermesAgent,
    HermesAgentChannelBinding,
    HermesAgentOperation,
    HermesAgentRuntimeState,
)
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeClient, HermesRuntimeError
from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion
from backend.app.newapi.service import LlmNewapiUserMappingService
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone

CHANNEL_DISPLAY = {
    'feishu': '飞书',
    'weixin': '微信',
    'qq': 'QQ',
    'wecom': '企业微信',
    'webhook': 'Webhook',
}
SECRET_KEYS = {
    'runtime_profile_id',
    'profile_path',
    'workspace_path',
    'api_server_host',
    'api_server_port',
    'api_key',
    'app_secret',
    'secret',
    'token',
    'runtime_token',
}


def _now() -> datetime:
    return timezone.now()


def _new(model: type, **values: Any) -> Any:
    try:
        return model(**values)
    except TypeError:
        obj = model()
        for key, value in values.items():
            setattr(obj, key, value)
        return obj


def _dt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_datetime(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return value


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if key in SECRET_KEYS or key.endswith(('_secret', '_token')):
                continue
            safe[key] = _safe_json(item)
        return safe
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_object(value: Any) -> dict[str, Any]:
    """将运行时返回值收紧为键为字符串的 JSON 对象。"""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _host_workspace_display(workspace_path: str | None, agent_id: str) -> str | None:
    if not workspace_path:
        return None
    if agent_id in workspace_path:
        return f'/data/huanxing-hermes/workspaces/{agent_id}'
    parts = workspace_path.rstrip('/').split('/')
    return f'.../{parts[-1]}' if parts and parts[-1] else None


class HermesAgentAppService:
    def __init__(self, *, runtime_client: Any | None = None, id_factory: Callable[[], str] | None = None) -> None:
        self.runtime_client = runtime_client or HermesRuntimeClient()
        self.id_factory = id_factory or self._generate_agent_id

    @staticmethod
    def _generate_agent_id() -> str:
        return f'agt_{secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]}'

    async def _get_owned_agent(self, db: AsyncSession, *, user_id: int, agent_id: str, include_deleted: bool = False) -> Any:
        if hasattr(db, 'hermes_agents'):
            agent = next((item for item in db.hermes_agents if item.agent_id == agent_id and item.user_id == user_id and (include_deleted or item.deleted_time is None)), None)
        else:
            conditions = [HermesAgent.agent_id == agent_id, HermesAgent.user_id == user_id]
            if not include_deleted:
                conditions.append(HermesAgent.deleted_time.is_(None))
            result = await db.execute(sa.select(HermesAgent).where(*conditions))
            agent = result.scalar_one_or_none()
        if not agent:
            raise errors.NotFoundError(msg='Hermes Agent 不存在')
        return agent

    async def _get_runtime_state(self, db: AsyncSession, agent_id: str) -> Any | None:
        if hasattr(db, 'runtime_states'):
            return next((item for item in db.runtime_states if item.agent_id == agent_id), None)
        result = await db.execute(sa.select(HermesAgentRuntimeState).where(HermesAgentRuntimeState.agent_id == agent_id))
        return result.scalar_one_or_none()

    async def _get_channels(self, db: AsyncSession, *, user_id: int, agent_id: str) -> list[Any]:
        if hasattr(db, 'channel_bindings'):
            return [item for item in db.channel_bindings if item.user_id == user_id and item.agent_id == agent_id]
        result = await db.execute(
            sa.select(HermesAgentChannelBinding).where(
                HermesAgentChannelBinding.user_id == user_id,
                HermesAgentChannelBinding.agent_id == agent_id,
            )
        )
        return list(result.scalars().all())

    async def _ensure_agent_name_available(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        agent_name: str,
        exclude_agent_id: str | None = None,
    ) -> None:
        if hasattr(db, 'hermes_agents'):
            exists = any(
                item.user_id == user_id
                and item.agent_name == agent_name
                and item.deleted_time is None
                and item.agent_id != exclude_agent_id
                for item in db.hermes_agents
            )
        else:
            conditions = [
                HermesAgent.user_id == user_id,
                HermesAgent.agent_name == agent_name,
                HermesAgent.deleted_time.is_(None),
            ]
            if exclude_agent_id:
                conditions.append(HermesAgent.agent_id != exclude_agent_id)
            result = await db.execute(sa.select(HermesAgent.agent_id).where(*conditions).limit(1))
            exists = result.scalar_one_or_none() is not None
        if exists:
            raise errors.ConflictError(msg='Agent 名称已存在，请换一个名称')

    async def _record_operation(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        agent_id: str,
        operation_type: str,
        operation_status: str,
        trace_id: str | None,
        request_summary: dict[str, Any] | None = None,
        response_summary: dict[str, Any] | None = None,
        error: HermesRuntimeError | None = None,
        started_at: datetime | None = None,
    ) -> Any:
        op = _new(
            HermesAgentOperation,
            operation_id=f'op_{uuid4().hex[:20]}',
            agent_id=agent_id,
            user_id=user_id,
            operation_type=operation_type,
            operation_status=operation_status,
            runtime_request_id=trace_id,
            started_at=started_at or _now(),
            finished_at=_now(),
            request_summary_json=_safe_json(request_summary) if request_summary else None,
            response_summary_json=_safe_json(response_summary) if response_summary else None,
            error_json=error.to_response_data() if error else None,
        )
        db.add(op)
        await db.flush()
        return op

    def _agent_card(
        self,
        agent: Any,
        state: Any | None = None,
        channels: list[Any] | None = None,
        *,
        template_version: str | None = None,
    ) -> dict[str, Any]:
        channels = channels or []
        return {
            'agent_id': agent.agent_id,
            'agent_name': agent.agent_name,
            'status': agent.status,
            'gateway_status': agent.gateway_status,
            'terminal_backend': getattr(state, 'terminal_backend', None) or 'docker',
            'container_workspace': getattr(state, 'container_workspace', None) or '/workspace',
            'workspace_status': agent.workspace_status,
            'llm_mode': agent.llm_mode,
            'llm_model': agent.llm_model,
            'template': agent.template,
            'template_version': template_version,
            'channel_summary': [
                {
                    'channel': item.channel,
                    'status': item.status,
                    'display_name': item.display_name or CHANNEL_DISPLAY.get(item.channel, item.channel),
                    'updated_at': _dt(getattr(item, 'updated_time', None)),
                }
                for item in channels
            ],
            'last_active_at': _dt(agent.last_active_at),
            'created_at': _dt(agent.created_time),
            'updated_at': _dt(agent.updated_time or agent.created_time),
        }

    def _agent_detail(self, agent: Any, state: Any | None, channels: list[Any]) -> dict[str, Any]:
        return {
            'agent_id': agent.agent_id,
            'agent_name': agent.agent_name,
            'template': agent.template,
            'timezone': agent.timezone,
            'status': agent.status,
            'gateway': {
                'status': agent.gateway_status,
                'api_server_reachable': getattr(state, 'api_server_reachable', True) if state else True,
                'restart_count': getattr(state, 'gateway_restart_count', 0) if state else 0,
                'started_at': _dt(getattr(state, 'gateway_started_at', None)) if state else None,
                'last_error': agent.last_error_message,
            },
            'workspace': {
                'status': agent.workspace_status,
                'container_workspace': getattr(state, 'container_workspace', None) or '/workspace',
                'host_workspace_display': getattr(state, 'host_workspace_display', None) if state else None,
                'file_count': getattr(state, 'workspace_file_count', 0) if state else 0,
                'bytes_used': getattr(state, 'workspace_bytes_used', 0) if state else 0,
                'last_write_at': _dt(getattr(state, 'workspace_last_write_at', None)) if state else None,
            },
            'sandbox': {
                'terminal_backend': getattr(state, 'terminal_backend', None) or 'docker',
                'network_policy': getattr(state, 'network_policy', None) or 'public_outbound_internal_denied',
                'mount_policy': getattr(state, 'mount_policy', None) or 'workspace_only',
                'ready': bool(getattr(state, 'network_ready', True)) if state else True,
                'last_error': getattr(state, 'last_error_message', None) if state else None,
            },
            'llm': {
                'mode': agent.llm_mode,
                'provider': agent.llm_provider,
                'model': agent.llm_model,
                'api_key_configured': agent.llm_mode == 'platform',
            },
            'channels': [self._channel_public(item) for item in channels],
            'last_error': {'code': agent.last_error_code, 'message': agent.last_error_message} if agent.last_error_code else None,
            'created_at': _dt(agent.created_time),
            'updated_at': _dt(agent.updated_time or agent.created_time),
        }

    @staticmethod
    def _channel_public(item: Any) -> dict[str, Any]:
        return {
            'channel': item.channel,
            'display_name': item.display_name or CHANNEL_DISPLAY.get(item.channel, item.channel),
            'enabled': item.channel in {'feishu', 'weixin', 'qq'},
            'bind_mode': 'qr_or_manual' if item.channel in {'feishu', 'qq'} else 'qr',
            'status': item.status,
            'bound_account_display': item.bound_account_display,
            'last_error': item.last_error_message,
            'metadata': _safe_json(item.metadata_json or {}),
            'updated_at': _dt(getattr(item, 'updated_time', None)),
        }

    async def _resolve_template(self, db: AsyncSession, template_id: str) -> dict[str, Any]:
        """查询 Agent 模板及其最新版本，供运行时应用模板。"""
        if not template_id:
            raise errors.NotFoundError(msg='template_not_found')
        stmt = (
            sa.select(
                MarketplaceTemplate.template_id,
                MarketplaceTemplate.name,
                MarketplaceTemplate.description,
                MarketplaceTemplate.emoji,
                MarketplaceTemplate.icon_url,
                MarketplaceTemplate.skill_dependencies,
                MarketplaceTemplateVersion.version,
                MarketplaceTemplateVersion.package_url,
                MarketplaceTemplateVersion.file_hash,
            )
            .join(
                MarketplaceTemplateVersion,
                MarketplaceTemplateVersion.template_id == MarketplaceTemplate.template_id,
            )
            .where(
                MarketplaceTemplate.template_id == template_id,
                MarketplaceTemplate.template_type == 'agent',
                MarketplaceTemplateVersion.is_latest.is_(True),
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).mappings().first()
        if not row:
            raise errors.NotFoundError(msg='template_not_found')
        return {
            'template_id': row['template_id'],
            'name': row['name'],
            'description': row['description'],
            'emoji': row['emoji'],
            'icon_url': row['icon_url'],
            'skill_dependencies': row['skill_dependencies'],
            'version': row['version'],
            'package_url': row['package_url'],
            'file_hash': row['file_hash'],
        }

    async def create_agent(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        payload: Any,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if getattr(payload, 'llm_mode', 'platform') == 'byok':
            raise errors.RequestError(msg='MVP 暂不支持 BYOK 模式，请使用 platform 模式')
        agent_id = self.id_factory()
        now = _now()
        await self._ensure_agent_name_available(db, user_id=user_id, agent_name=payload.agent_name)
        template_id = payload.template or 'assistant'
        template = await self._resolve_template(db, template_id)
        llm_model = getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_MODEL', None) or getattr(
            settings, 'HERMES_PLATFORM_LLM_MODEL', 'openai/gpt-5.5'
        )
        agent = _new(
            HermesAgent,
            agent_id=agent_id,
            user_id=user_id,
            agent_name=payload.agent_name,
            template=template['template_id'],
            timezone=payload.timezone or 'Asia/Shanghai',
            status='creating',
            llm_mode='platform',
            llm_provider='openai_compatible',
            llm_model=llm_model,
            gateway_status='stopped',
            workspace_status='unknown',
            sandbox_status='unknown',
            channel_count=0,
            created_time=now,
            updated_time=now,
        )
        db.add(agent)
        await db.flush()

        runtime_payload = {
            'agent_id': agent_id,
            'owner_user_id': str(user_id),
            'agent_name': payload.agent_name,
            'timezone': payload.timezone or 'Asia/Shanghai',
            'template': payload.template or 'assistant',
            'llm': {
                'mode': 'platform',
                'provider': 'openai_compatible',
                'base_url': getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_BASE_URL', 'https://api.huanxing.ai/api/v1/llm/proxy/v1'),
                'api_key': getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_API_KEY', ''),
                'model': llm_model,
                'plan_id': getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_PLAN_ID', 'pro_monthly'),
            },
            'sandbox': {
                'terminal_backend': 'docker',
                'docker_image': 'nikolaik/python-nodejs:python3.11-nodejs20',
                'network_policy': 'public_outbound_internal_denied',
            },
        }
        started_at = now
        try:
            runtime = await self.runtime_client.ensure_agent(runtime_payload, trace_id=trace_id)
            runtime_profile_id = runtime['runtime_profile_id']
            agent.runtime_profile_id = runtime_profile_id
            agent.profile_name = runtime.get('profile_name')
            agent.runtime_id = runtime.get('runtime_id') or getattr(settings, 'HUANXING_HERMES_RUNTIME_ID', 'hermes-runtime-local')
            agent.status = 'created'
            agent.workspace_status = runtime.get('workspace_status') or 'ready'
            agent.sandbox_status = 'ready'
            agent.last_runtime_sync_at = _now()

            # 查 owner user 拿 nickname/phone（hub 模板里 USER.md / SOUL.md / MEMORY.md 用到）
            if hasattr(db, 'users'):
                user_row = next((u for u in db.users if getattr(u, 'id', None) == user_id), None)
            elif hasattr(db, 'execute'):
                user_result = await db.execute(sa.select(User).where(User.id == user_id))
                user_row = user_result.scalar_one_or_none()
            else:
                user_row = None
            user_nickname = (getattr(user_row, 'nickname', None) if user_row else None) or payload.agent_name
            user_phone = (getattr(user_row, 'phone', None) if user_row else None) or ''

            apply_payload: dict[str, Any] = {
                'template_id': template['template_id'],
                'template_version': template['version'],
                'package_url': template.get('package_url'),
                'file_hash': template.get('file_hash'),
                'render_context': {
                    'agent_name': payload.agent_name,
                    'owner_user_id': str(user_id),
                    'locale': getattr(payload, 'locale', None) or 'zh-CN',
                    'timezone': payload.timezone or 'Asia/Shanghai',
                    'now': now.isoformat(),
                    # 以下字段对齐 hub 仓 _base/owner/USER.md.template + 各模板 SOUL.md
                    'createdAt': now.isoformat(),
                    'nickname': user_nickname,
                    'phone': user_phone,
                },
            }
            if getattr(payload, 'soul', None):
                apply_payload['soul_append'] = payload.soul
            if getattr(payload, 'user_profile', None):
                apply_payload['user_append'] = payload.user_profile
            await self.runtime_client.apply_template(runtime_profile_id, apply_payload, trace_id=trace_id)

            # 经 new-api 管理 API 签发 Agent 级 token（client 模块单例，无需 DB 会话）
            issued = await LlmNewapiUserMappingService.ensure_agent_token(
                db, agent_id=agent_id, user_id=user_id,
            )
            raw_token_key = issued.get('raw_token_key')
            if raw_token_key:
                credential_payload = {
                    'token_key': f'sk-{raw_token_key}',
                    'base_url': getattr(
                        settings,
                        'HUANXING_HERMES_PLATFORM_LLM_BASE_URL',
                        'https://api.huanxing.ai/api/v1/llm/proxy/v1',
                    ),
                    'default_model': llm_model,
                }
                try:
                    await self.runtime_client.install_credential(
                        runtime_profile_id, credential_payload, trace_id=trace_id,
                    )
                except HermesRuntimeError:
                    # 失败回滚链：撤销刚签发的 token，再删 runtime profile（皆 swallow）
                    try:
                        await LlmNewapiUserMappingService.revoke_agent_token(db, agent_id)
                    except Exception:
                        pass
                    try:
                        await self.runtime_client.delete_agent(runtime_profile_id, trace_id=trace_id)
                    except Exception:
                        pass
                    raise

            if payload.soul is not None:
                await self.runtime_client.put_soul(runtime_profile_id, payload.soul, trace_id=trace_id)
            if payload.user_profile is not None:
                await self.runtime_client.put_user_profile(runtime_profile_id, payload.user_profile, trace_id=trace_id)

            gateway = None
            if payload.auto_start_gateway:
                gateway = await self.runtime_client.start_gateway(runtime_profile_id, trace_id=trace_id)
                agent.gateway_status = gateway.get('status', 'running')
                agent.status = 'running' if agent.gateway_status == 'running' else 'created'
            else:
                agent.status = 'created'
                agent.gateway_status = 'stopped'

            state = _new(
                HermesAgentRuntimeState,
                agent_id=agent_id,
                runtime_id=agent.runtime_id,
                runtime_profile_id=runtime_profile_id,
                profile_name=agent.profile_name,
                gateway_status=agent.gateway_status,
                gateway_restart_count=(gateway or {}).get('restart_count', 0),
                gateway_started_at=_parse_datetime((gateway or {}).get('started_at')),
                api_server_reachable=(gateway or {}).get('api_server_reachable', True),
                terminal_backend='docker',
                container_workspace=runtime.get('container_workspace') or '/workspace',
                host_workspace_display=_host_workspace_display(runtime.get('workspace_path'), agent_id),
                workspace_status=agent.workspace_status,
                workspace_file_count=0,
                workspace_bytes_used=0,
                workspace_last_write_at=None,
                mount_policy='workspace_only',
                network_policy='public_outbound_internal_denied',
                network_ready=True,
                runtime_snapshot=_safe_json(runtime),
                last_health_at=_now(),
            )
            db.add(state)
            agent.updated_time = _now()
            await self._record_operation(
                db,
                user_id=user_id,
                agent_id=agent_id,
                operation_type='create_agent',
                operation_status='succeeded',
                trace_id=trace_id,
                request_summary=runtime_payload,
                response_summary=runtime,
                started_at=started_at,
            )
            await db.flush()
            return self._agent_card(agent, state, [], template_version=template['version'])
        except HermesRuntimeError as exc:
            agent.status = 'error'
            agent.last_error_code = exc.error
            agent.last_error_message = exc.details
            agent.updated_time = _now()
            await self._record_operation(
                db,
                user_id=user_id,
                agent_id=agent_id,
                operation_type='create_agent',
                operation_status='failed',
                trace_id=trace_id,
                request_summary=runtime_payload,
                error=exc,
                started_at=started_at,
            )
            await db.flush()
            raise

    async def list_agents(self, db: AsyncSession, *, user_id: int, status: str | None = None, channel: str | None = None, page: int = 1, size: int = 20) -> dict[str, Any]:
        if hasattr(db, 'hermes_agents'):
            agents_all = [item for item in db.hermes_agents if item.user_id == user_id and item.deleted_time is None]
            if status:
                agents_all = [item for item in agents_all if item.status == status]
            if channel:
                test_db = cast(Any, db)
                allowed = {
                    item.agent_id
                    for item in test_db.channel_bindings
                    if item.user_id == user_id and item.channel == channel
                }
                agents_all = [item for item in agents_all if item.agent_id in allowed]
            agents_all.sort(key=lambda item: item.id or 0, reverse=True)
            total = len(agents_all)
            agents = agents_all[(page - 1) * size:(page - 1) * size + size]
        else:
            conditions = [HermesAgent.user_id == user_id, HermesAgent.deleted_time.is_(None)]
            if status:
                conditions.append(HermesAgent.status == status)
            stmt = sa.select(HermesAgent).where(*conditions).order_by(HermesAgent.id.desc())
            if channel:
                stmt = stmt.join(HermesAgentChannelBinding, HermesAgentChannelBinding.agent_id == HermesAgent.agent_id).where(
                    HermesAgentChannelBinding.channel == channel
                )
            total = (await db.execute(sa.select(sa.func.count()).select_from(stmt.subquery()))).scalar_one()
            result = await db.execute(stmt.offset((page - 1) * size).limit(size))
            agents = list(result.scalars().all())
        items = []
        for agent in agents:
            state = await self._get_runtime_state(db, agent.agent_id)
            channels = await self._get_channels(db, user_id=user_id, agent_id=agent.agent_id)
            items.append(self._agent_card(agent, state, channels))
        return {'items': items, 'total': total, 'page': page, 'size': size}

    async def get_agent_detail(self, db: AsyncSession, *, user_id: int, agent_id: str) -> dict[str, Any]:
        agent = await self._get_owned_agent(db, user_id=user_id, agent_id=agent_id)
        state = await self._get_runtime_state(db, agent_id)
        channels = await self._get_channels(db, user_id=user_id, agent_id=agent_id)
        # 实时刷 runtime 真实 gateway 状态，避免 DB 缓存导致 UI 显示 fake 绿色
        if getattr(agent, 'runtime_profile_id', None):
            try:
                data = await self.runtime_client.get_gateway_status(agent.runtime_profile_id)
                runtime_status = data.get('status', agent.gateway_status)
                agent.gateway_status = runtime_status
                agent.status = 'running' if runtime_status == 'running' else 'created'
                if state is not None:
                    state.gateway_status = runtime_status
                    state.api_server_reachable = bool(data.get('api_server_reachable', runtime_status == 'running'))
                    state.last_health_at = _now()
            except HermesRuntimeError:
                # runtime 不可达：诚实标 unknown，让前端显示出错状态而非 fake 绿色
                agent.gateway_status = 'unknown'
                agent.status = 'error'
                if state is not None:
                    state.gateway_status = 'unknown'
                    state.api_server_reachable = False
        return self._agent_detail(agent, state, channels)

    async def update_agent(self, db: AsyncSession, *, user_id: int, agent_id: str, payload: Any, trace_id: str | None = None) -> dict[str, Any]:
        agent = await self._get_owned_agent(db, user_id=user_id, agent_id=agent_id)
        updates = payload.model_dump(exclude_unset=True) if hasattr(payload, 'model_dump') else dict(payload)
        if updates.get('agent_name') and updates['agent_name'] != agent.agent_name:
            await self._ensure_agent_name_available(
                db,
                user_id=user_id,
                agent_name=updates['agent_name'],
                exclude_agent_id=agent_id,
            )
        for field in ('agent_name', 'timezone'):
            if field in updates and updates[field] is not None:
                setattr(agent, field, updates[field])
        agent.updated_time = _now()
        await self._record_operation(db, user_id=user_id, agent_id=agent_id, operation_type='update_agent', operation_status='succeeded', trace_id=trace_id, request_summary=updates)
        await db.flush()
        return await self.get_agent_detail(db, user_id=user_id, agent_id=agent_id)

    async def delete_agent(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        agent_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Reverse cleanup（PROMPT.md §5.3）：
        stop_gateway → uninstall_credential → revoke_agent_token → runtime.delete_agent
        → local record(deleted_time=now)。每一步 runtime/token 调用 swallow，因为：
        - gateway 可能本来就停
        - credential 可能没装（apply_template 之前就失败的 agent）
        - token 可能没签发（从未签发的 agent）
        - runtime profile 可能已不在
        本地 record 写不动属真错误，仍然抛。
        """
        agent = await self._get_owned_agent(db, user_id=user_id, agent_id=agent_id)
        agent.status = 'deleting'
        agent.updated_time = _now()

        runtime_profile_id = agent.runtime_profile_id
        # 1. stop gateway
        if runtime_profile_id:
            try:
                await self.runtime_client.stop_gateway(runtime_profile_id, trace_id=trace_id)
            except HermesRuntimeError:
                pass
        # 2. uninstall credential
        if runtime_profile_id:
            try:
                await self.runtime_client.uninstall_credential(runtime_profile_id, trace_id=trace_id)
            except HermesRuntimeError:
                pass
        # 3. revoke agent token（经 new-api 管理 API，swallow——可能从未签发）
        try:
            await LlmNewapiUserMappingService.revoke_agent_token(db, agent_id)
        except Exception:
            pass
        # 4. runtime.delete_agent
        if runtime_profile_id:
            try:
                await self.runtime_client.delete_agent(runtime_profile_id, trace_id=trace_id)
            except HermesRuntimeError:
                pass

        # 5. local record finalize
        agent.status = 'deleted'
        agent.deleted_time = _now()
        agent.gateway_status = 'stopped'
        agent.updated_time = _now()
        await self._record_operation(
            db,
            user_id=user_id,
            agent_id=agent_id,
            operation_type='delete_agent',
            operation_status='succeeded',
            trace_id=trace_id,
        )
        await db.flush()
        return {'agent_id': agent.agent_id, 'status': agent.status}

    async def runtime_profile_id(self, db: AsyncSession, *, user_id: int, agent_id: str) -> str:
        agent = await self._get_owned_agent(db, user_id=user_id, agent_id=agent_id)
        if not agent.runtime_profile_id:
            raise HermesRuntimeError(error='runtime_profile_missing', details='agent has no runtime profile', action='recreate agent')
        return agent.runtime_profile_id

    async def get_persona(self, db: AsyncSession, *, user_id: int, agent_id: str, kind: str, trace_id: str | None = None) -> dict[str, Any]:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await (self.runtime_client.get_soul(profile_id, trace_id=trace_id) if kind == 'soul' else self.runtime_client.get_user_profile(profile_id, trace_id=trace_id))
        return {'agent_id': agent_id, 'kind': kind, 'content': data.get('content', ''), 'updated_at': data.get('updated_at'), 'effective_policy': 'next_message'}

    async def put_persona(self, db: AsyncSession, *, user_id: int, agent_id: str, kind: str, content: str, trace_id: str | None = None) -> dict[str, Any]:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await (self.runtime_client.put_soul(profile_id, content, trace_id=trace_id) if kind == 'soul' else self.runtime_client.put_user_profile(profile_id, content, trace_id=trace_id))
        await self._record_operation(db, user_id=user_id, agent_id=agent_id, operation_type='update_agent', operation_status='succeeded', trace_id=trace_id, request_summary={'kind': kind})
        return {'agent_id': agent_id, 'kind': kind, 'content': content, 'updated_at': data.get('updated_at'), 'effective_policy': 'next_message'}

    async def gateway(self, db: AsyncSession, *, user_id: int, agent_id: str, action: str, trace_id: str | None = None, limit: int = 100) -> Any:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        if action == 'status':
            data = await self.runtime_client.get_gateway_status(profile_id, trace_id=trace_id)
        elif action == 'logs':
            return await self.runtime_client.get_gateway_logs(profile_id, limit=limit, trace_id=trace_id)
        else:
            data = await getattr(self.runtime_client, f'{action}_gateway')(profile_id, trace_id=trace_id)
            agent = await self._get_owned_agent(db, user_id=user_id, agent_id=agent_id)
            agent.gateway_status = data.get('status', agent.gateway_status)
            agent.status = 'running' if agent.gateway_status == 'running' else 'stopped'
            agent.updated_time = _now()
            await self._record_operation(db, user_id=user_id, agent_id=agent_id, operation_type=f'{action}_gateway', operation_status='succeeded', trace_id=trace_id, response_summary=data)
        return {'agent_id': agent_id, **_safe_json(data)}

    async def workspace_status(self, db: AsyncSession, *, user_id: int, agent_id: str, trace_id: str | None = None) -> dict[str, Any]:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await self.runtime_client.get_workspace_status(profile_id, trace_id=trace_id)
        await self._sync_workspace_state(db, user_id=user_id, agent_id=agent_id, data=data)
        return {'agent_id': agent_id, **_safe_json(data), 'host_workspace_display': _host_workspace_display(data.get('workspace_path'), agent_id)}

    async def channels(self, db: AsyncSession, *, user_id: int, agent_id: str, trace_id: str | None = None) -> Any:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await self.runtime_client.get_channels(profile_id, trace_id=trace_id)
        raw_channels = data.get('channels', data) if isinstance(data, dict) else data
        channels = []
        if isinstance(raw_channels, list):
            for item in raw_channels:
                if not isinstance(item, dict):
                    continue
                channel = str(item.get('channel') or '')
                metadata = _json_object(item.get('metadata'))
                channels.append(
                    {
                        'channel': channel,
                        'display_name': CHANNEL_DISPLAY.get(channel, channel),
                        'enabled': channel in {'feishu', 'weixin', 'qq'},
                        'bind_mode': 'qr_or_manual' if channel in {'feishu', 'qq'} else 'qr',
                        'status': item.get('status', 'unbound'),
                        'bound_account_display': metadata.get('account_display') or metadata.get('open_id'),
                        'last_error': item.get('last_error'),
                        'metadata': _safe_json(metadata),
                        'updated_at': item.get('updated_at') or item.get('bound_at'),
                    }
                )
        return _safe_json(channels)

    async def _sync_workspace_state(self, db: AsyncSession, *, user_id: int, agent_id: str, data: dict[str, Any]) -> None:
        agent = await self._get_owned_agent(db, user_id=user_id, agent_id=agent_id)
        state = await self._get_runtime_state(db, agent_id)
        agent.workspace_status = data.get('status', agent.workspace_status)
        agent.last_runtime_sync_at = _now()
        agent.updated_time = _now()
        if state is not None:
            state.workspace_status = data.get('status', getattr(state, 'workspace_status', None))
            state.workspace_file_count = data.get('file_count', getattr(state, 'workspace_file_count', 0)) or 0
            state.workspace_bytes_used = data.get('bytes_used', getattr(state, 'workspace_bytes_used', 0)) or 0
            state.workspace_last_write_at = _parse_datetime(data.get('last_write_at')) or getattr(state, 'workspace_last_write_at', None)
            state.host_workspace_display = _host_workspace_display(data.get('workspace_path'), agent_id)
            state.container_workspace = data.get('container_workspace') or getattr(state, 'container_workspace', None) or '/workspace'
            state.runtime_snapshot = _safe_json(data)
            state.last_health_at = _now()
        await db.flush()

    async def channel_action(self, db: AsyncSession, *, user_id: int, agent_id: str, channel: str, action: str, payload: dict[str, Any] | None = None, session_id: str | None = None, trace_id: str | None = None) -> dict[str, Any]:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        payload = payload or {}
        if action == 'qr_start':
            data = await self.runtime_client.start_channel_qr(profile_id, channel, payload, trace_id=trace_id)
        elif action == 'qr_status':
            data = await self.runtime_client.get_channel_qr_status(profile_id, channel, session_id or '', trace_id=trace_id)
        elif action == 'manual':
            data = await self.runtime_client.manual_channel(profile_id, channel, payload, trace_id=trace_id)
        elif action == 'test':
            data = await self.runtime_client.test_channel(profile_id, channel, payload, trace_id=trace_id)
        elif action == 'unbind':
            data = await self.runtime_client.unbind_channel(profile_id, channel, payload, trace_id=trace_id)
        else:
            raise errors.NotFoundError(msg='Channel action 不存在')
        await self._upsert_channel_binding(db, user_id=user_id, agent_id=agent_id, channel=channel, data=data, action=action)
        await self._record_operation(db, user_id=user_id, agent_id=agent_id, operation_type='bind_channel' if action != 'unbind' else 'unbind_channel', operation_status='succeeded', trace_id=trace_id, request_summary=payload, response_summary=data)
        return _safe_json(data)

    async def _upsert_channel_binding(self, db: AsyncSession, *, user_id: int, agent_id: str, channel: str, data: dict[str, Any], action: str) -> None:
        if hasattr(db, 'channel_bindings'):
            item = next((row for row in db.channel_bindings if row.user_id == user_id and row.agent_id == agent_id and row.channel == channel), None)
        else:
            result = await db.execute(sa.select(HermesAgentChannelBinding).where(HermesAgentChannelBinding.user_id == user_id, HermesAgentChannelBinding.agent_id == agent_id, HermesAgentChannelBinding.channel == channel))
            item = result.scalar_one_or_none()
        if not item:
            item = _new(HermesAgentChannelBinding, binding_id=f'bind_{uuid4().hex[:20]}', agent_id=agent_id, user_id=user_id, channel=channel, bind_mode='qr' if action.startswith('qr') else 'manual', status='unbound', display_name=CHANNEL_DISPLAY.get(channel, channel))
            db.add(item)
        item.status = data.get('status', item.status)
        item.runtime_session_id = data.get('session_id', item.runtime_session_id)
        item.expires_at = _parse_datetime(data.get('expires_at')) or item.expires_at
        metadata = _json_object(data.get('metadata'))
        item.metadata_json = _safe_json(metadata)
        item.bound_account_display = metadata.get('account_display') or metadata.get('open_id') or item.bound_account_display
        await db.flush()

    async def chat_history(self, db: AsyncSession, *, user_id: int, agent_id: str, trace_id: str | None = None) -> dict[str, Any]:
        """读 hermes-agent sessions/ 下最新对话，给前端进入 chat 页时 rehydrate。"""
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await self.runtime_client.get_chat_history(profile_id, trace_id=trace_id)
        return _safe_json(data)

    async def chat_completions(self, db: AsyncSession, *, user_id: int, agent_id: str, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await self.runtime_client.chat_completions(profile_id, payload, trace_id=trace_id)
        await self._record_operation(db, user_id=user_id, agent_id=agent_id, operation_type='chat', operation_status='succeeded', trace_id=trace_id, request_summary={'stream': payload.get('stream', False)}, response_summary=data)
        try:
            workspace = await self.runtime_client.get_workspace_status(profile_id, trace_id=trace_id)
            await self._sync_workspace_state(db, user_id=user_id, agent_id=agent_id, data=workspace)
        except HermesRuntimeError:
            pass
        return _safe_json(data)

    async def stream_chat_completions(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        agent_id: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ):
        """SSE async generator: 透传 runtime SSE chunks 给浏览器（M1 §5.5）。

        endpoint 用 StreamingResponse 包装返回。每个 chunk 是 raw bytes
        (`b'data: {...}\\n\\n'` 或 `b'event: error\\n...'`)，不再二次 JSON encode。
        审计 record_operation 在结束后写一次（success / failed by frame）。
        """
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        had_error_frame = False
        try:
            async for chunk in self.runtime_client.chat_completions_stream(
                profile_id, payload, trace_id=trace_id,
            ):
                if chunk.startswith(b'event: error'):
                    had_error_frame = True
                yield chunk
        finally:
            await self._record_operation(
                db,
                user_id=user_id,
                agent_id=agent_id,
                operation_type='chat',
                operation_status='failed' if had_error_frame else 'succeeded',
                trace_id=trace_id,
                request_summary={'stream': True},
            )

    async def create_run(self, db: AsyncSession, *, user_id: int, agent_id: str, payload: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        data = await self.runtime_client.create_run(profile_id, payload, trace_id=trace_id)
        await self._record_operation(db, user_id=user_id, agent_id=agent_id, operation_type='run', operation_status='succeeded', trace_id=trace_id, request_summary=payload, response_summary=data)
        try:
            workspace = await self.runtime_client.get_workspace_status(profile_id, trace_id=trace_id)
            await self._sync_workspace_state(db, user_id=user_id, agent_id=agent_id, data=workspace)
        except HermesRuntimeError:
            pass
        return _safe_json(data)

    async def get_run_events(self, db: AsyncSession, *, user_id: int, agent_id: str, run_id: str, trace_id: str | None = None) -> Any:
        profile_id = await self.runtime_profile_id(db, user_id=user_id, agent_id=agent_id)
        return _safe_json(await self.runtime_client.get_run_events(profile_id, run_id, trace_id=trace_id))


hermes_agent_app_service = HermesAgentAppService()
