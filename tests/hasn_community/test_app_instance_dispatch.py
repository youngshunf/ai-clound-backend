"""设计 14-AI-Native应用平台/11 —— P1+P2 真实联调（连真实 PG，事务回滚隔离，零 Mock 零 Fake）。

覆盖：
- InstanceResolver.resolve 对内置工具返回 gateway_internal 句柄（endpoint=云端自身）
- _dispatch_tool 去硬编码：经 instance_resolver + internal_handlers 注册表真实路由社区 feed（不再 `if app_id==`）
- 去掉安装态：无 workspace_app 记录默认可用；仅企业 override status=disabled 时不可用
- 未知 transport → 15052；cloud_relay 尚未配置实例 → 15050（P3 落地真实签名转发）
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading

from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.app.hasn.model import HasnAppInstance, HasnWorkspaceApp
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway as gateway
from backend.app.hasn.service.app_instance_service import app_instance_service
from backend.app.hasn.service.instance_resolver import (
    FACE_TOOL,
    TRANSPORT_GATEWAY_INTERNAL,
    InstanceResolutionError,
    instance_resolver,
)
from backend.app.llm.core.encryption import key_encryption
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.utils.timezone import timezone
from tests.hasn_community.conftest import seed_agent, seed_human, seed_post


def _agent_payload(owner: dict, agent_row: dict) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=agent_row['hasn_id'],
        agent_name=agent_row['display_name'],
        owner_hasn_id=owner['hasn_id'],
        owner_user_id=owner['user_id'],
        scopes=['community:read', 'community:post', 'community:comment', 'community:interact'],
        session_uuid='sess-test-app-instance',
        expire_time=timezone.now() + timedelta(hours=1),
    )


def _personal_ws(owner: dict) -> dict:
    return {
        'kind': 'personal',
        'user_id': owner['user_id'],
        'enterprise_id': None,
        'workspace_key': f"personal:{owner['user_id']}",
    }


@pytest.mark.asyncio
async def test_resolve_builtin_tool_returns_gateway_internal_handle(db):
    """内置工具解析为 gateway_internal：endpoint=云端自身、无外部凭据（设计 11 §2.2/§3.1）。"""
    manifest = await ai_native_app_registry.ensure_builtin_published(db, 'community')
    handle = await instance_resolver.resolve(
        db, app_id='community', workspace={'kind': 'personal'},
        face=FACE_TOOL, manifest=manifest, tool_id='community.get_feed',
    )
    assert handle.transport == TRANSPORT_GATEWAY_INTERNAL
    assert handle.is_internal is True
    assert handle.endpoint is None
    assert handle.credential is None


@pytest.mark.asyncio
async def test_dispatch_community_feed_via_registry_not_hardcoded(db):
    """_dispatch_tool 经注册表真实路由社区 feed（消灭 `if app_id==` 硬编码），返回真实数据。

    这是"Agent 调用工具出错"的直接修复点：去硬编码后内置工具仍全链路调通。
    """
    owner = await seed_human(db, nickname='实例主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='实例分身')
    post_id = await seed_post(db, author_hasn_id=owner['hasn_id'], content='主人的帖子（recommend 流应可见）')

    manifest = await ai_native_app_registry.ensure_builtin_published(db, 'community')
    result = await gateway._dispatch_tool(
        db,
        app_id='community',
        tool_id='community.get_feed',
        workspace=_personal_ws(owner),
        agent=_agent_payload(owner, agent_row),
        input_payload={'type': 'recommend', 'limit': 20},
        manifest=manifest,
    )
    # 经注册表路由到真实 handle_community_get_feed → community_service.get_feed，返回真实契约形状
    # （'posts'/'cursor'/'has_more'），证明去硬编码后内置工具仍真实取数（非 stub / 非空填充）。
    assert isinstance(result, dict)
    assert isinstance(result.get('posts'), list)
    assert 'has_more' in result and 'cursor' in result
    assert result['posts'], 'recommend 流应返回真实帖子（证明去硬编码后真实路由生效）'
    _ = post_id  # 已落库真实帖子；recommend 排序不保证置顶，断言真实契约即可


@pytest.mark.asyncio
async def test_internal_handler_registry_covers_knowledge_and_community(db):
    """handler 注册表覆盖知识库 + 社区全量工具（取代两套硬编码分支）。"""
    registry = gateway._internal_handlers()
    assert 'knowledge.search' in registry and callable(registry['knowledge.search'])
    for tool_id in ('community.get_feed', 'community.get_post', 'community.create_post', 'community.create_doc_node'):
        assert tool_id in registry and callable(registry[tool_id]), tool_id


@pytest.mark.asyncio
async def test_workspace_app_available_by_default_without_record(db):
    """去掉安装态：个人空间无 workspace_app 记录即默认可用（published 即用，设计 11 §4.2）。"""
    owner = await seed_human(db, nickname='零配置主人')
    available = await gateway._is_workspace_app_available(
        db, workspace=_personal_ws(owner), app_id='community'
    )
    assert available is True


@pytest.mark.asyncio
async def test_workspace_app_enterprise_override_disabled_blocks(db):
    """企业 override status=disabled → 不可用；status=active / 无记录 → 可用（设计 11 §4.2/§4.3）。"""
    ent_disabled = 990001
    ent_active = 990002
    db.add(HasnWorkspaceApp(workspace_kind='enterprise', enterprise_id=ent_disabled, app_id='community', status='disabled'))
    db.add(HasnWorkspaceApp(workspace_kind='enterprise', enterprise_id=ent_active, app_id='community', status='active'))
    await db.flush()

    assert await gateway._is_workspace_app_available(
        db, workspace={'kind': 'enterprise', 'enterprise_id': ent_disabled}, app_id='community'
    ) is False
    assert await gateway._is_workspace_app_available(
        db, workspace={'kind': 'enterprise', 'enterprise_id': ent_active}, app_id='community'
    ) is True
    # 无记录的企业空间同样回落为可用
    assert await gateway._is_workspace_app_available(
        db, workspace={'kind': 'enterprise', 'enterprise_id': 990999}, app_id='community'
    ) is True


@pytest.mark.asyncio
async def test_resolve_unknown_transport_raises_15052(db):
    """manifest 声明非法 transport → 15052（该 transport 不允许此调用面）。"""
    fake = {'app_id': 'x', 'manifest_json': {'tools': [{'tool_id': 'x.do', 'transport': 'bogus', 'handler': 'x.do'}]}}
    with pytest.raises(InstanceResolutionError) as ei:
        await instance_resolver.resolve(
            db, app_id='x', workspace={'kind': 'personal'}, face=FACE_TOOL, manifest=fake, tool_id='x.do'
        )
    assert ei.value.code == '15052'


@pytest.mark.asyncio
async def test_dispatch_cloud_relay_without_instance_raises_15050(db):
    """cloud_relay 工具但尚无实例行 → 15050（实例未配置）；证明第三方 transport 被识别，P3 落地真实转发。"""
    owner = await seed_human(db, nickname='第三方主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='第三方分身')
    fake = {
        'app_id': 'faketp',
        'manifest_json': {
            'tools': [{'tool_id': 'faketp.do', 'transport': 'cloud_relay', 'handler': 'faketp.do'}],
            'capabilities': [],
        },
    }
    with pytest.raises(InstanceResolutionError) as ei:
        await gateway._dispatch_tool(
            db,
            app_id='faketp',
            tool_id='faketp.do',
            workspace=_personal_ws(owner),
            agent=_agent_payload(owner, agent_row),
            input_payload={},
            manifest=fake,
        )
    assert ei.value.code == '15050'


# ============ P3：实例落库 + cloud_relay 真实签名转发 + 第三方注册（设计 11 §3/§5/§6）============


def _make_third_party_handler(secret: str):
    """真实第三方应用 HTTP handler：按 HExt-08 §16.4 验 HMAC 签名后回真实结果（零 mock）。"""

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length)
            signing = '\n'.join(
                [
                    self.headers.get('X-HASN-App-Id', ''),
                    self.headers.get('X-HASN-Timestamp', ''),
                    self.headers.get('X-HASN-Nonce', ''),
                    body.decode('utf-8'),
                ]
            )
            expected = hmac.new(secret.encode('utf-8'), signing.encode('utf-8'), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, self.headers.get('X-HASN-Signature', '')):
                self._respond(401, {'error': 'bad_signature'})
                return
            payload = json.loads(body)
            self._respond(
                200, {'ok': True, 'verified': True, 'echo': payload.get('input'), 'tool_id': payload.get('tool_id')}
            )

        def _respond(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # 静音访问日志
            pass

    return _Handler


def _start_fake_third_party(secret: str):
    server = HTTPServer(('127.0.0.1', 0), _make_third_party_handler(secret))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}'


@pytest.mark.asyncio
async def test_cloud_relay_signed_forward_to_real_third_party(db):
    """cloud_relay 全链路：resolver 读实例 → 解密 app secret → HMAC 签名 → POST 真实第三方 → 验签回结果。

    证明第三方 Tool 能路由出去（即"Agent 调用工具出错"结构性原因的根治），零 mock。
    """
    secret = 'tp-shared-secret-绝密-123'
    server, base_url = _start_fake_third_party(secret)
    try:
        owner = await seed_human(db, nickname='第三方调用主人')
        agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='第三方调用分身')
        # 直接落公共实例行（endpoint=本地真实服务器；credential_ref=加密的共享密钥）
        db.add(
            HasnAppInstance(
                app_id='relaytp', scope='public', enterprise_id=None, endpoint=base_url,
                transport_default='cloud_relay', credential_ref=key_encryption.encrypt(secret),
                status='active', config={},
            )
        )
        await db.flush()
        manifest = {
            'app_id': 'relaytp',
            'manifest_json': {
                'tools': [{'tool_id': 'relaytp.echo', 'transport': 'cloud_relay', 'handler': 'relaytp.echo'}],
                'capabilities': [],
            },
        }
        result = await gateway._dispatch_tool(
            db,
            app_id='relaytp',
            tool_id='relaytp.echo',
            workspace=_personal_ws(owner),
            agent=_agent_payload(owner, agent_row),
            input_payload={'msg': 'hello-relay'},
            manifest=manifest,
        )
        assert result['ok'] is True
        assert result['verified'] is True  # 第三方真实验过 HMAC 签名
        assert result['echo'] == {'msg': 'hello-relay'}
        assert result['tool_id'] == 'relaytp.echo'
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_register_app_binds_ownership_and_rejects_non_owner(db):
    """第三方注册即接入：绑定所有权 + 建公共实例 + 发布 manifest → resolver 立即可解析；非 owner 重注册被拒。"""
    dev1 = await seed_human(db, nickname='开发者1')
    dev2 = await seed_human(db, nickname='开发者2')
    manifest = {
        'app_id': 'crm',
        'version': '1.0.0',
        'workspace_scope': ['personal'],
        'tools': [{'tool_id': 'crm.lead.create', 'transport': 'cloud_relay', 'handler': 'crm.lead.create'}],
    }
    res = await app_instance_service.register_app(
        db, developer_id=dev1['hasn_id'], app_id='crm', manifest_json=manifest,
        endpoint='https://crm.example.com/hasn', credential='crm-secret',
    )
    assert res['status'] == 'published'

    # 发布即用：resolver 从已发布 manifest 取 transport + 解析公共实例（解密凭据）
    handle = await instance_resolver.resolve(
        db, app_id='crm', workspace={'kind': 'personal'}, face=FACE_TOOL, tool_id='crm.lead.create'
    )
    assert handle.transport == 'cloud_relay'
    assert handle.endpoint == 'https://crm.example.com/hasn'
    assert (handle.credential or {}).get('value') == 'crm-secret'

    # 所有权校验：非 owner 不能改他人 App（设计 11 §5.2）
    with pytest.raises(errors.ForbiddenError):
        await app_instance_service.register_app(
            db, developer_id=dev2['hasn_id'], app_id='crm', manifest_json=manifest,
            endpoint='https://evil.example.com/hasn', credential='x',
        )


@pytest.mark.asyncio
async def test_register_app_rejects_non_https_endpoint(db):
    """设计 11 §10：第三方 endpoint 强制 HTTPS。"""
    dev = await seed_human(db, nickname='不安全开发者')
    with pytest.raises(errors.RequestError):
        await app_instance_service.register_app(
            db, developer_id=dev['hasn_id'], app_id='insecure', manifest_json={'app_id': 'insecure'},
            endpoint='http://insecure.example.com', credential='x',
        )


@pytest.mark.asyncio
async def test_resolver_enterprise_overrides_public_with_fallback(db):
    """企业自建 active → 企业实例；企业未建 → 回落公共；个人 → 公共（设计 11 §3.2）。"""
    db.add(HasnAppInstance(app_id='kbx', scope='public', endpoint='https://pub.example.com',
                           transport_default='cloud_relay', credential_ref=key_encryption.encrypt('pub'),
                           status='active', config={}))
    db.add(HasnAppInstance(app_id='kbx', scope='enterprise', enterprise_id=7001, endpoint='https://ent.example.com',
                           transport_default='cloud_relay', credential_ref=key_encryption.encrypt('ent'),
                           status='active', config={}))
    await db.flush()
    manifest = {'app_id': 'kbx', 'manifest_json': {'tools': [
        {'tool_id': 'kbx.q', 'transport': 'cloud_relay', 'handler': 'kbx.q'}]}}

    h_ent = await instance_resolver.resolve(
        db, app_id='kbx', workspace={'kind': 'enterprise', 'enterprise_id': 7001},
        face=FACE_TOOL, manifest=manifest, tool_id='kbx.q')
    assert h_ent.scope == 'enterprise' and h_ent.endpoint == 'https://ent.example.com'

    h_fallback = await instance_resolver.resolve(
        db, app_id='kbx', workspace={'kind': 'enterprise', 'enterprise_id': 9999},
        face=FACE_TOOL, manifest=manifest, tool_id='kbx.q')
    assert h_fallback.scope == 'public' and h_fallback.endpoint == 'https://pub.example.com'

    h_personal = await instance_resolver.resolve(
        db, app_id='kbx', workspace={'kind': 'personal'}, face=FACE_TOOL, manifest=manifest, tool_id='kbx.q')
    assert h_personal.scope == 'public'


@pytest.mark.asyncio
async def test_configure_enterprise_instance_resolves_for_that_enterprise(db):
    """企业配置自建实例后，该企业空间解析到企业实例（其它空间仍回落公共，设计 11 §5.3）。"""
    dev = await seed_human(db, nickname='kby开发者')
    await app_instance_service.register_app(
        db, developer_id=dev['hasn_id'], app_id='kby',
        manifest_json={'app_id': 'kby', 'version': '1.0.0',
                       'tools': [{'tool_id': 'kby.q', 'transport': 'cloud_relay', 'handler': 'kby.q'}]},
        endpoint='https://pub.kby.com', credential='pub')
    await app_instance_service.configure_enterprise_instance(
        db, app_id='kby', enterprise_id=8001, endpoint='https://ent.kby.com', credential='ent')

    h = await instance_resolver.resolve(
        db, app_id='kby', workspace={'kind': 'enterprise', 'enterprise_id': 8001}, face=FACE_TOOL, tool_id='kby.q')
    assert h.scope == 'enterprise' and h.endpoint == 'https://ent.kby.com'
    assert (h.credential or {}).get('value') == 'ent'
