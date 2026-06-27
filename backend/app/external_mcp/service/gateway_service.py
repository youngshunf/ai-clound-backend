"""第三方 MCP 网关编排服务（P7 主线，事实源 10/02）。

把「配置 → 自省 → 渐进暴露 → 代理调用」串成一条链路：
- 注册：校验 transport×hosting + namespace + 明文凭据，落 server 行（origin/hosting/transport/endpoint/headers）；
- 自省：RemoteMcpClient.list_tools → 归一 ToolMeta[] → 算 tools_hash → 缓存进 server 行 + 健康；
- 绑定：owner 授权某 Agent 可用该 server 的哪些工具（allowed_tools = raw↔canonical 映射）；
- 解析：给定 (agent, owner) 返回其可发现/可调的 external 工具集（gate1 owner 启用 + gate2 agent binding）；
- 代理：tools/call 前做 binding/health/allowed/secret/quota 校验 → 注入 secret → 调第三方 → 归一 → 记账。

边界（10 §10）：本服务只做「协议代理 + 工具目录缓存」，**绝不**把第三方返回结构化写入业务库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time

from typing import Any

from sqlalchemy import delete, or_, select

from backend.app.external_mcp.model import ExternalMcpBinding, ExternalMcpServer
from backend.app.external_mcp.service.mcp_client import ExternalMcpClientError, RemoteMcpClient
from backend.app.external_mcp.service.quota import quota_service
from backend.app.external_mcp.service.secret_store import secret_store
from backend.app.external_mcp.service.validation import (
    RegistrationError,
    validate_credential_values,
    validate_server_namespace,
    validate_transport_hosting,
)
from backend.app.mcp.canonical import schema_hash, validate_canonical_name
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

_RAW_NAME_SAFE = str.maketrans({'-': '_', '.': '_', '/': '_', ' ': '_'})

# 嵌入在 header/env 值里的 secret:// 引用 token（用于明文替换，如 'Bearer secret://...'）。
_SECRET_REF_TOKEN = re.compile(r'secret://[A-Za-z0-9._/\-]+')

# 凭据在 server 内的确定性 key（管理面写入/轮换/撤销复用同一 secret_uri）。
_CREDENTIAL_KEY = 'credential'


def _new_id(prefix: str) -> str:
    """生成 {prefix}_{时间有序随机} 业务主键（无 ulid 依赖，时间前缀保证大致有序）。"""
    millis = int(time.time() * 1000)
    return f'{prefix}_{millis:013d}{secrets.token_hex(6)}'


def _legalize_segment(raw: str) -> str:
    """第三方原始工具名合法化为 canonical 段（非法字符替换）。"""
    legalized = raw.translate(_RAW_NAME_SAFE)
    legalized = ''.join(ch for ch in legalized if ch.isalnum() or ch == '_')
    return legalized or 'tool'


class ExternalMcpGateway:
    """第三方 MCP 网关（云端 remote_service 主线）。"""

    # ---------- 归一 ----------

    def normalize_tool(self, *, server_name: str, raw_tool: dict[str, Any], origin: str) -> dict[str, Any]:
        """第三方 tools/list 单条 → HASN ToolMeta（10 §3）。"""
        raw_name = str(raw_tool.get('name') or '').strip()
        if not raw_name:
            raise ValueError('第三方工具缺少 name')
        action = _legalize_segment(raw_name)
        canonical = f'hasn.ext.{server_name}.{action}'
        # 校验映射名合法（不合法直接抛，注册期暴露）。
        validate_canonical_name(canonical, 'external')
        input_schema = raw_tool.get('inputSchema') or raw_tool.get('input_schema') or {'type': 'object'}
        description = str(raw_tool.get('description') or raw_tool.get('title') or raw_name)
        # 写操作/副作用工具默认 medium 起步（10 §3）；annotations.readOnlyHint 为 True 才降 low。
        annotations = raw_tool.get('annotations') or {}
        read_only = bool(annotations.get('readOnlyHint')) if isinstance(annotations, dict) else False
        risk_level = 'low' if read_only else 'medium'
        return {
            'name': canonical,
            'source': 'external',
            'execution_location': 'cloud',
            'raw_name': raw_name,
            'title': str(raw_tool.get('title') or raw_name),
            'summary': description[:500],
            'input_schema': input_schema,
            'required_scopes': [f'mcp:tool://{canonical}'],
            'risk_level': risk_level,
            'schema_hash': schema_hash(input_schema if isinstance(input_schema, dict) else {}),
            'origin': origin,
        }

    @staticmethod
    def compute_tools_hash(tool_metas: list[dict[str, Any]]) -> str:
        """工具集指纹（按 name+schema_hash 排序稳定计算，变更触发 re-probe / 9210）。"""
        material = sorted((m['name'], m.get('schema_hash', '')) for m in tool_metas)
        canonical = json.dumps(material, sort_keys=True, separators=(',', ':'))
        return 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    # ---------- 注册 ----------

    async def register_server(
        self,
        *,
        name: str,
        hosting: str,
        transport: str,
        origin: str = 'owner',
        owner_hasn_id: str | None = None,
        endpoint: str | None = None,
        command: str | None = None,
        args: list | None = None,
        env: dict | None = None,
        headers: dict | None = None,
        display_name: str | None = None,
        scope: str = 'owner',
        risk_level: str = 'medium',
        per_owner_daily_quota: int = 0,
        rate_limit_per_min: int = 0,
    ) -> dict[str, Any]:
        """注册第三方 MCP server（校验 → 落库）。返回 server dict。"""
        name = validate_server_namespace(name)
        validate_transport_hosting(hosting=hosting, transport=transport, endpoint=endpoint, command=command)
        validate_credential_values(headers, where='headers')
        validate_credential_values(env, where='env')
        if origin in {'owner', 'marketplace'} and not owner_hasn_id:
            raise RegistrationError(f'{origin}-origin server 必须指定 owner_hasn_id')

        mcp_id = _new_id('mcp')
        async with async_db_session.begin() as db:
            existing = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.name == name))
            ).scalar_one_or_none()
            if existing is not None:
                raise McpToolError(
                    McpErrorCode.TOOL_NAME_CONFLICT,
                    f"server_namespace '{name}' 已被占用（10 §4 冲突规则）",
                )
            server = ExternalMcpServer(
                mcp_id=mcp_id,
                name=name,
                display_name=display_name or name,
                hosting=hosting,
                transport=transport,
                endpoint=endpoint,
                command=command,
                args=args or [],
                env=env or {},
                headers=headers or {},
                origin=origin,
                owner_hasn_id=owner_hasn_id,
                scope=scope,
                risk_level=risk_level,
                advertised_tools_cache=[],
                health_status='unknown',
                per_owner_daily_quota=per_owner_daily_quota,
                rate_limit_per_min=rate_limit_per_min,
                status='active',
            )
            db.add(server)
            await db.flush()
            return self._server_to_dict(server)

    # ---------- 自省 ----------

    async def _resolve_headers(self, headers: dict[str, Any]) -> dict[str, str]:
        """把 headers 里的 secret:// 引用解析成明文（仅建连内部用，绝不日志/回显）。

        支持**嵌入式**引用：`Authorization: 'Bearer secret://system/qcc/credential'`
        会把其中的 `secret://...` 段替换成明文 → `Bearer <token>`（不止整值替换）。
        任一引用解析为 None（未配置/已撤销）→ CREDENTIAL_MISSING（撤销后软挡的实现点）。
        """
        resolved: dict[str, str] = {}
        for key, value in (headers or {}).items():
            if not isinstance(value, str):
                continue
            refs = _SECRET_REF_TOKEN.findall(value)
            if not refs:
                resolved[str(key)] = value
                continue
            out = value
            for ref in refs:
                plaintext = await secret_store.resolve(ref)
                if plaintext is None:
                    raise McpToolError(
                        McpErrorCode.CREDENTIAL_MISSING,
                        f'凭据 {ref} 不可解析（未配置或已撤销），请重新配置',
                    )
                out = out.replace(ref, plaintext)
            resolved[str(key)] = out
        return resolved

    async def introspect_server(self, mcp_id: str) -> dict[str, Any]:
        """自省第三方 server：tools/list → 归一 → 缓存 + 健康。仅 remote_service 在云端自省。"""
        server = await self._get_server(mcp_id)
        if server is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'server 不存在: {mcp_id}')
        if server['hosting'] != 'remote_service':
            raise RegistrationError('云端自省仅支持 remote_service；local_process 由设备本地 probe（02 §7）')

        health = 'healthy'
        detail = None
        tool_metas: list[dict[str, Any]] = []
        try:
            headers = await self._resolve_headers(server['headers'])
            client = RemoteMcpClient(endpoint=server['endpoint'], headers=headers)
            raw_tools = await client.list_tools()
            tool_metas = [
                self.normalize_tool(server_name=server['name'], raw_tool=t, origin=server['origin'])
                for t in raw_tools
            ]
        except (ExternalMcpClientError, McpToolError) as exc:
            health = 'unhealthy'
            detail = str(exc)
            logger.warning('introspect %s 失败: %s', mcp_id, detail)

        tools_hash = self.compute_tools_hash(tool_metas) if tool_metas else None
        await self._update_server_introspection(
            mcp_id, tool_metas=tool_metas, tools_hash=tools_hash, health=health, detail=detail
        )
        return {'mcp_id': mcp_id, 'health': health, 'tools_hash': tools_hash, 'tools': tool_metas, 'detail': detail}

    # ---------- 绑定 ----------

    async def create_binding(
        self,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str,
        mcp_id: str,
        allowed_raw_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """owner 授权某 Agent 可用该 server（allowed_raw_names=None → 全量授权）。"""
        server = await self._get_server(mcp_id)
        if server is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'server 不存在: {mcp_id}')

        advertised = server.get('advertised_tools_cache') or []
        wanted = set(allowed_raw_names) if allowed_raw_names is not None else None
        allowed_tools = [
            {'raw_name': meta['raw_name'], 'tool_name': meta['name']}
            for meta in advertised
            if wanted is None or meta['raw_name'] in wanted
        ]

        binding_id = _new_id('mcb')
        async with async_db_session.begin() as db:
            existing = (
                await db.execute(
                    select(ExternalMcpBinding).where(
                        ExternalMcpBinding.agent_hasn_id == agent_hasn_id,
                        ExternalMcpBinding.mcp_id == mcp_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.enabled = True
                existing.allowed_tools = allowed_tools
                existing.owner_hasn_id = owner_hasn_id
                binding_id = existing.binding_id
            else:
                db.add(
                    ExternalMcpBinding(
                        binding_id=binding_id,
                        agent_hasn_id=agent_hasn_id,
                        owner_hasn_id=owner_hasn_id,
                        mcp_id=mcp_id,
                        enabled=True,
                        allowed_tools=allowed_tools,
                    )
                )
        return {'binding_id': binding_id, 'mcp_id': mcp_id, 'agent_hasn_id': agent_hasn_id, 'allowed_tools': allowed_tools}

    async def set_binding_enabled(self, *, agent_hasn_id: str, mcp_id: str, enabled: bool) -> bool:
        async with async_db_session.begin() as db:
            binding = (
                await db.execute(
                    select(ExternalMcpBinding).where(
                        ExternalMcpBinding.agent_hasn_id == agent_hasn_id,
                        ExternalMcpBinding.mcp_id == mcp_id,
                    )
                )
            ).scalar_one_or_none()
            if binding is None:
                return False
            binding.enabled = enabled
        return True

    # ---------- 解析（发现）----------

    async def resolve_agent_external_tools(
        self, *, agent_hasn_id: str, owner_hasn_id: str | None
    ) -> list[dict[str, Any]]:
        """返回该 Agent 可发现/可调的 external 工具描述（gate1 owner 启用 + gate2 binding）。

        每条含 name / raw_name / mcp_id / input_schema / summary / required_scopes / risk_level / origin。
        仅返回 server 启用 + binding enabled + allowed_tools 命中的工具。
        """
        async with async_db_session() as db:
            bindings = (
                await db.execute(
                    select(ExternalMcpBinding).where(
                        ExternalMcpBinding.agent_hasn_id == agent_hasn_id,
                        ExternalMcpBinding.enabled.is_(True),
                    )
                )
            ).scalars().all()
            if not bindings:
                return []
            mcp_ids = {b.mcp_id for b in bindings}
            servers = (
                await db.execute(
                    select(ExternalMcpServer).where(
                        ExternalMcpServer.mcp_id.in_(mcp_ids),
                        ExternalMcpServer.status == 'active',
                    )
                )
            ).scalars().all()
        server_by_id = {s.mcp_id: s for s in servers}

        tools: list[dict[str, Any]] = []
        for binding in bindings:
            server = server_by_id.get(binding.mcp_id)
            if server is None:
                continue
            # gate1: owner-origin server 必须属于本 owner；system-origin 全平台可见。
            if server.origin in {'owner', 'marketplace'} and owner_hasn_id and server.owner_hasn_id != owner_hasn_id:
                continue
            cache_by_raw = {m['raw_name']: m for m in (server.advertised_tools_cache or [])}
            for entry in binding.allowed_tools or []:
                meta = cache_by_raw.get(entry['raw_name'])
                if meta is None:
                    continue
                tools.append(
                    {
                        'name': meta['name'],
                        'raw_name': meta['raw_name'],
                        'mcp_id': server.mcp_id,
                        'input_schema': meta.get('input_schema') or {'type': 'object'},
                        'summary': meta.get('summary') or '',
                        'required_scopes': meta.get('required_scopes') or [f"mcp:tool://{meta['name']}"],
                        'risk_level': meta.get('risk_level', 'medium'),
                        'origin': server.origin,
                    }
                )
        return tools

    # ---------- 代理调用 ----------

    async def proxy_call(
        self,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """代理一次 external 工具调用（10 §6 全校验链 + 平台 key 治理 + 记账）。"""
        ns = self._namespace_of(tool_name)
        server = await self._get_server_by_name(ns)
        if server is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'external server 不存在: {ns}')
        if server['status'] != 'active':
            raise McpToolError(McpErrorCode.DIRECT_CALL_DENIED, f"server '{ns}' 已停用")

        binding = await self._get_binding(agent_hasn_id=agent_hasn_id, mcp_id=server['mcp_id'])
        if binding is None or not binding['enabled']:
            raise McpToolError(
                McpErrorCode.DIRECT_CALL_DENIED,
                f'Agent 未绑定或已停用该 external server（{ns}）',
            )

        # raw 必须在 allowed_tools（10 §6）。
        raw_name = None
        for entry in binding['allowed_tools'] or []:
            if entry['tool_name'] == tool_name:
                raw_name = entry['raw_name']
                break
        if raw_name is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_ALLOWED, f'工具未在该 Agent 的授权集合: {tool_name}')

        # 健康（远程不可达即 unhealthy/circuit_broken）。
        if server['health_status'] == 'circuit_broken':
            raise McpToolError(McpErrorCode.SERVER_CIRCUIT_BROKEN, f"server '{ns}' 已熔断，暂不可用")

        # tools_hash 校验：缓存里仍能找到该工具（schema 漂移 → 9210 + re-probe）。
        cache_by_canonical = {m['name']: m for m in (server.get('advertised_tools_cache') or [])}
        if tool_name not in cache_by_canonical:
            # 触发 re-probe 一次再判定。
            await self.introspect_server(server['mcp_id'])
            server = await self._get_server(server['mcp_id']) or server
            cache_by_canonical = {m['name']: m for m in (server.get('advertised_tools_cache') or [])}
            if tool_name not in cache_by_canonical:
                raise McpToolError(
                    McpErrorCode.SCHEMA_HASH_MISMATCH,
                    f'工具 {tool_name} 在第三方已变更/下线（tools_hash 不一致），已触发 re-probe',
                )

        # 平台 key 配额/限流（system-origin）。
        await quota_service.enforce(
            mcp_id=server['mcp_id'],
            origin=server['origin'],
            owner_hasn_id=owner_hasn_id,
            per_owner_daily_quota=server['per_owner_daily_quota'],
            rate_limit_per_min=server['rate_limit_per_min'],
        )

        # 注入 secret → 调第三方 → 归一。
        success = False
        try:
            headers = await self._resolve_headers(server['headers'])
            client = RemoteMcpClient(endpoint=server['endpoint'], headers=headers)
            raw_result = await client.call_tool(raw_name, arguments)
            success = True
            normalized = self._normalize_call_result(raw_result)
            return normalized
        except ExternalMcpClientError as exc:
            raise McpToolError(McpErrorCode.SERVER_UNHEALTHY, f'第三方 MCP 调用失败: {exc}') from exc
        finally:
            await quota_service.record(
                mcp_id=server['mcp_id'],
                origin=server['origin'],
                caller_owner_hasn_id=owner_hasn_id,
                caller_agent_hasn_id=agent_hasn_id,
                tool_name=tool_name,
                trace_id=trace_id,
                success=success,
            )

    @staticmethod
    def _normalize_call_result(raw_result: dict[str, Any]) -> dict[str, Any]:
        """第三方 tools/call result → HASN tool result（保留原始结构，10 §6）。"""
        content = raw_result.get('content')
        structured = raw_result.get('structuredContent') or raw_result.get('structured_content')
        is_error = bool(raw_result.get('isError'))
        # 抽纯文本便于上层消费（保留原始 content/structured）。
        text_parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text' and item.get('text'):
                    text_parts.append(str(item['text']))
        return {
            'ok': not is_error,
            'is_error': is_error,
            'text': '\n'.join(text_parts) if text_parts else None,
            'content': content,
            'structured': structured,
            'raw': raw_result,
        }

    # ---------- 管理面（owner/admin 配置 + 凭据生命周期，10 §7.1 / P7-D）----------

    def _credential_uri(self, server: dict[str, Any]) -> str:
        """该 server 的确定性凭据 secret_uri（写入/轮换/撤销复用同一引用）。"""
        return secret_store.build_uri(
            origin=server['origin'],
            owner_hasn_id=server.get('owner_hasn_id'),
            server=server['name'],
            key=_CREDENTIAL_KEY,
        )

    @staticmethod
    def _server_to_public_dict(server: ExternalMcpServer) -> dict[str, Any]:
        """管理面出参：剔除 headers/env 值（可能含 secret:// 引用），只暴露是否已配凭据 + 工具计数。"""
        headers = server.headers or {}
        tools = server.advertised_tools_cache or []
        return {
            'mcp_id': server.mcp_id,
            'name': server.name,
            'display_name': server.display_name,
            'hosting': server.hosting,
            'transport': server.transport,
            'endpoint': server.endpoint,
            'origin': server.origin,
            'owner_hasn_id': server.owner_hasn_id,
            'risk_level': server.risk_level,
            'health_status': server.health_status,
            'health_detail': server.health_detail,
            'tools_count': len(tools) if isinstance(tools, list) else 0,
            'tools_hash': server.tools_hash,
            'credential_configured': any('secret://' in str(v) for v in headers.values()),
            'credential_header': next((str(k) for k, v in headers.items() if 'secret://' in str(v)), None),
            'per_owner_daily_quota': server.per_owner_daily_quota,
            'rate_limit_per_min': server.rate_limit_per_min,
            'status': server.status,
        }

    async def list_servers(self, *, owner_hasn_id: str, include_system: bool = True) -> list[dict[str, Any]]:
        """列出 owner 自配 server（+ 可选 system 共享 server，供绑定）。"""
        async with async_db_session() as db:
            if include_system:
                where = or_(
                    ExternalMcpServer.owner_hasn_id == owner_hasn_id,
                    ExternalMcpServer.origin == 'system',
                )
            else:
                where = ExternalMcpServer.owner_hasn_id == owner_hasn_id
            rows = (
                await db.execute(select(ExternalMcpServer).where(where).order_by(ExternalMcpServer.id.desc()))
            ).scalars().all()
        return [self._server_to_public_dict(r) for r in rows]

    async def list_servers_admin(self, *, origin: str = 'system') -> list[dict[str, Any]]:
        """Admin 列出某 origin 的 server（默认 system 平台预置）。"""
        async with async_db_session() as db:
            rows = (
                await db.execute(
                    select(ExternalMcpServer).where(ExternalMcpServer.origin == origin).order_by(ExternalMcpServer.id.desc())
                )
            ).scalars().all()
        return [self._server_to_public_dict(r) for r in rows]

    async def get_server_detail(
        self, *, mcp_id: str, owner_hasn_id: str | None = None, is_admin: bool = False
    ) -> dict[str, Any] | None:
        """server 详情（含 advertised_tools）。owner 行级隔离：只能看自己的 + system。"""
        async with async_db_session() as db:
            row = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
            ).scalar_one_or_none()
        if row is None:
            return None
        if not is_admin and owner_hasn_id is not None:
            if row.owner_hasn_id != owner_hasn_id and row.origin != 'system':
                return None
        detail = self._server_to_public_dict(row)
        detail['advertised_tools'] = row.advertised_tools_cache or []
        return detail

    def _check_server_manage_perm(self, server: dict[str, Any], *, owner_hasn_id: str | None, is_admin: bool) -> None:
        """配置/删除/凭据写入的权限闸：admin 管 system；owner 管自己的 owner/marketplace server。"""
        if is_admin:
            if server['origin'] != 'system':
                raise McpToolError(McpErrorCode.DIRECT_CALL_DENIED, 'admin 仅管理 system-origin server')
            return
        if server['origin'] not in {'owner', 'marketplace'} or server.get('owner_hasn_id') != owner_hasn_id:
            raise McpToolError(McpErrorCode.DIRECT_CALL_DENIED, '无权管理该 external MCP server')

    async def set_credential(
        self,
        *,
        mcp_id: str,
        plaintext: str,
        owner_hasn_id: str | None = None,
        is_admin: bool = False,
        auth_header: str = 'Authorization',
        auth_scheme: str = 'Bearer',
    ) -> dict[str, Any]:
        """写入/轮换凭据：明文 → 加密落库（确定性 URI）→ 把 header 模板写进 server.headers。明文永不回显。"""
        server = await self._get_server(mcp_id)
        if server is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'server 不存在: {mcp_id}')
        self._check_server_manage_perm(server, owner_hasn_id=owner_hasn_id, is_admin=is_admin)
        secret_uri = self._credential_uri(server)
        await secret_store.write(
            secret_uri=secret_uri,
            plaintext=plaintext,
            origin=server['origin'],
            owner_hasn_id=server.get('owner_hasn_id'),
        )
        header_value = f'{auth_scheme} {secret_uri}'.strip() if auth_scheme else secret_uri
        async with async_db_session.begin() as db:
            row = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
            ).scalar_one_or_none()
            if row is not None:
                headers = dict(row.headers or {})
                headers[auth_header] = header_value
                row.headers = headers
        return {'mcp_id': mcp_id, 'credential_configured': True, 'auth_header': auth_header}

    async def revoke_credential(
        self, *, mcp_id: str, owner_hasn_id: str | None = None, is_admin: bool = False
    ) -> dict[str, Any]:
        """撤销凭据：删密文（header 模板保留）→ 后续建连解析失败 = 软挡提示重配（10 §7.1）。"""
        server = await self._get_server(mcp_id)
        if server is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'server 不存在: {mcp_id}')
        self._check_server_manage_perm(server, owner_hasn_id=owner_hasn_id, is_admin=is_admin)
        removed = await secret_store.revoke(self._credential_uri(server))
        return {'mcp_id': mcp_id, 'revoked': removed}

    async def set_server_status(
        self, *, mcp_id: str, status: str, owner_hasn_id: str | None = None, is_admin: bool = False
    ) -> dict[str, Any]:
        """启用/停用 server（owner 管自己的，admin 管 system）。"""
        if status not in {'active', 'disabled'}:
            raise McpToolError(McpErrorCode.CONFIG_SCHEMA_INVALID, f'非法 status: {status}')
        server = await self._get_server(mcp_id)
        if server is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'server 不存在: {mcp_id}')
        self._check_server_manage_perm(server, owner_hasn_id=owner_hasn_id, is_admin=is_admin)
        async with async_db_session.begin() as db:
            row = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
            ).scalar_one_or_none()
            if row is not None:
                row.status = status
        return {'mcp_id': mcp_id, 'status': status}

    async def set_server_quota(
        self, *, mcp_id: str, per_owner_daily_quota: int, rate_limit_per_min: int
    ) -> dict[str, Any]:
        """Admin 配 system-origin 平台 key 的 per-owner 配额/限流（10 §7.2）。"""
        async with async_db_session.begin() as db:
            row = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
            ).scalar_one_or_none()
            if row is None:
                raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'server 不存在: {mcp_id}')
            if row.origin != 'system':
                raise McpToolError(McpErrorCode.DIRECT_CALL_DENIED, '配额仅适用于 system-origin 平台 key')
            row.per_owner_daily_quota = max(0, int(per_owner_daily_quota))
            row.rate_limit_per_min = max(0, int(rate_limit_per_min))
        return {
            'mcp_id': mcp_id,
            'per_owner_daily_quota': max(0, int(per_owner_daily_quota)),
            'rate_limit_per_min': max(0, int(rate_limit_per_min)),
        }

    async def delete_server(
        self, *, mcp_id: str, owner_hasn_id: str | None = None, is_admin: bool = False
    ) -> bool:
        """删除 server：撤销凭据 + 删该 server 全部 binding + 删 server 行。"""
        server = await self._get_server(mcp_id)
        if server is None:
            return False
        self._check_server_manage_perm(server, owner_hasn_id=owner_hasn_id, is_admin=is_admin)
        await secret_store.revoke(self._credential_uri(server))
        async with async_db_session.begin() as db:
            await db.execute(delete(ExternalMcpBinding).where(ExternalMcpBinding.mcp_id == mcp_id))
            await db.execute(delete(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
        return True

    async def list_bindings(self, *, owner_hasn_id: str) -> list[dict[str, Any]]:
        """列出该 owner 名下全部 Agent↔server 绑定（管理面展示）。"""
        async with async_db_session() as db:
            rows = (
                await db.execute(
                    select(ExternalMcpBinding)
                    .where(ExternalMcpBinding.owner_hasn_id == owner_hasn_id)
                    .order_by(ExternalMcpBinding.id.desc())
                )
            ).scalars().all()
        return [
            {
                'binding_id': r.binding_id,
                'agent_hasn_id': r.agent_hasn_id,
                'mcp_id': r.mcp_id,
                'enabled': r.enabled,
                'allowed_tools': r.allowed_tools or [],
            }
            for r in rows
        ]

    # ---------- DB helpers ----------

    @staticmethod
    def _namespace_of(tool_name: str) -> str:
        parts = tool_name.split('.')
        if len(parts) >= 3 and parts[0] == 'hasn' and parts[1] == 'ext':
            return parts[2]
        raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'非 external 工具名: {tool_name}')

    async def _get_server(self, mcp_id: str) -> dict[str, Any] | None:
        async with async_db_session() as db:
            row = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
            ).scalar_one_or_none()
        return self._server_to_dict(row) if row else None

    async def _get_server_by_name(self, name: str) -> dict[str, Any] | None:
        async with async_db_session() as db:
            row = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.name == name))
            ).scalar_one_or_none()
        return self._server_to_dict(row) if row else None

    async def _get_binding(self, *, agent_hasn_id: str, mcp_id: str) -> dict[str, Any] | None:
        async with async_db_session() as db:
            row = (
                await db.execute(
                    select(ExternalMcpBinding).where(
                        ExternalMcpBinding.agent_hasn_id == agent_hasn_id,
                        ExternalMcpBinding.mcp_id == mcp_id,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return {
            'binding_id': row.binding_id,
            'agent_hasn_id': row.agent_hasn_id,
            'owner_hasn_id': row.owner_hasn_id,
            'mcp_id': row.mcp_id,
            'enabled': row.enabled,
            'allowed_tools': row.allowed_tools,
        }

    async def _update_server_introspection(
        self,
        mcp_id: str,
        *,
        tool_metas: list[dict[str, Any]],
        tools_hash: str | None,
        health: str,
        detail: str | None,
    ) -> None:
        from backend.utils.timezone import timezone as tz

        async with async_db_session.begin() as db:
            server = (
                await db.execute(select(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
            ).scalar_one_or_none()
            if server is None:
                return
            if tool_metas:
                server.advertised_tools_cache = tool_metas
                server.tools_hash = tools_hash
                server.advertised_tools_cached_at = tz.now()
            server.health_status = health
            server.health_checked_at = tz.now()
            server.health_detail = detail

    @staticmethod
    def _server_to_dict(server: ExternalMcpServer) -> dict[str, Any]:
        return {
            'id': server.id,
            'mcp_id': server.mcp_id,
            'name': server.name,
            'display_name': server.display_name,
            'hosting': server.hosting,
            'transport': server.transport,
            'endpoint': server.endpoint,
            'command': server.command,
            'args': server.args,
            'env': server.env,
            'headers': server.headers,
            'origin': server.origin,
            'owner_hasn_id': server.owner_hasn_id,
            'scope': server.scope,
            'risk_level': server.risk_level,
            'advertised_tools_cache': server.advertised_tools_cache,
            'tools_hash': server.tools_hash,
            'health_status': server.health_status,
            'health_detail': server.health_detail,
            'per_owner_daily_quota': server.per_owner_daily_quota,
            'rate_limit_per_min': server.rate_limit_per_min,
            'status': server.status,
        }


external_mcp_gateway = ExternalMcpGateway()
