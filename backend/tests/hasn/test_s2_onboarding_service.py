from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.hasn.schema.hasn_onboarding import (
    ClientInfo,
    NodeClaim,
    OnboardingEnsureRequest,
    PhoneSendCodeRequest,
    PhoneVerifyRequest,
    SandboxSummary,
)
from backend.app.hasn.service import hasn_onboarding_service as onboarding_service_module
from backend.app.hasn.service.hasn_onboarding_service import (
    DEFAULT_AGENT_DISPLAY_NAME,
    PRIVATE_NODE_INFO_KEYS,
    SMS_CODE_PREFIX,
    HasnOnboardingService,
    HasnPhoneAuthService,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}
        self.deleted: list[str] = []

    async def exists(self, key: str) -> bool:
        return key in self.values

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, 0)

    async def setex(self, key: str, seconds: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = seconds

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)
        self.ttls.pop(key, None)


class FakeSms:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_code(self, phone: str, code: str) -> bool:
        self.sent.append((phone, code))
        return True


@dataclass
class FakeUser:
    id: int
    username: str
    nickname: str
    phone: str
    avatar: str | None = None
    bio: str | None = None
    is_multi_login: bool = True
    last_login_time: Any = None


class FakeUserGateway:
    def __init__(self) -> None:
        self.users: dict[str, FakeUser] = {}

    async def get_or_create_phone_user(self, db: Any, phone: str) -> tuple[FakeUser, bool]:
        if phone in self.users:
            return self.users[phone], False
        user = FakeUser(id=len(self.users) + 1, username=phone, nickname='手机用户', phone=phone)
        self.users[phone] = user
        return user, True


class FakeDb:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


class FakeLlmCredentialIssuer:
    async def issue(self, db: Any, user: Any) -> tuple[str, str, str]:
        return f'sk-user-{user.id}', 'https://llm.example/v1', 'test-model'


class FakeAgentTokenIssuer:
    async def issue(
        self,
        db: Any,
        *,
        agent_hasn_id: str,
        agent_name: str,
        owner_hasn_id: str,
        owner_user_id: int,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            access_token=f'agent-token:{agent_hasn_id}',
            access_token_expire_time=SimpleNamespace(isoformat=lambda: '2026-05-18T00:00:00+00:00'),
            expires_at_unix=1779062400,
            scopes=['message.read', 'knowledge.read'],
        )


@pytest.mark.asyncio
async def test_phone_send_code_reuses_sms_window_and_returns_contract_shape() -> None:
    redis = FakeRedis()
    sms = FakeSms()
    service = HasnPhoneAuthService(redis=redis, sms=sms, code_generator=lambda: '123456')

    first = await service.send_code(PhoneSendCodeRequest(phone='13800138000'))
    second = await service.send_code(PhoneSendCodeRequest(phone='13800138000'))

    assert first.ok is True
    assert first.retry_after_sec == 0
    assert redis.values[f'{SMS_CODE_PREFIX}:13800138000'] == '123456'
    assert sms.sent == [('13800138000', '123456')]
    assert second.ok is False
    assert second.retry_after_sec == 60


@pytest.mark.asyncio
async def test_phone_verify_creates_platform_user_and_issues_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    await redis.setex(f'{SMS_CODE_PREFIX}:13800138000', 1800, b'654321')
    users = FakeUserGateway()
    db = FakeDb()
    captured_token_kwargs: dict[str, Any] = {}

    async def fake_token_creator(user_id: int, *, multi_login: bool, **kwargs: Any) -> SimpleNamespace:
        captured_token_kwargs.update({'user_id': user_id, 'multi_login': multi_login, **kwargs})
        return SimpleNamespace(access_token='jwt-token', session_uuid='session-phone-verify')

    async def fake_refresh_token_creator(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(refresh_token='refresh-token')

    monkeypatch.setattr(onboarding_service_module, 'create_refresh_token', fake_refresh_token_creator)

    service = HasnPhoneAuthService(
        redis=redis,
        sms=FakeSms(),
        users=users,
        token_expire_seconds=86400,
        token_creator=fake_token_creator,
        llm_credentials=FakeLlmCredentialIssuer(),
    )

    response = await service.verify(
        db, PhoneVerifyRequest(phone='13800138000', code='654321', pending_intent_id='pi_123')
    )

    assert response.access_token == 'jwt-token'
    assert response.token_type == 'Bearer'
    assert response.expires_in_sec == 86400
    assert response.llm_token == 'sk-user-1'
    assert response.llm_base_url == 'https://llm.example/v1'
    assert response.llm_model == 'test-model'
    assert f'{SMS_CODE_PREFIX}:13800138000' in redis.deleted
    assert db.flush_count == 1
    assert captured_token_kwargs['pending_intent_id'] == 'pi_123'
    assert captured_token_kwargs['hasn_onboarding'] is True


@pytest.mark.asyncio
async def test_phone_verify_returns_agent_tokens_when_owner_has_active_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    await redis.setex(f'{SMS_CODE_PREFIX}:13800138000', 1800, b'654321')
    users = FakeUserGateway()
    db = FakeDb()

    async def fake_token_creator(user_id: int, *, multi_login: bool, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(access_token='jwt-token', session_uuid='session-phone-verify')

    async def fake_refresh_token_creator(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(refresh_token='refresh-token')

    async def fake_get_human(_db: Any, user_id: int) -> SimpleNamespace | None:
        if user_id != 1:
            return None
        return SimpleNamespace(hasn_id='h_owner_1')

    async def fake_get_active_agents(_db: Any, owner_hasn_id: str) -> list[SimpleNamespace]:
        assert owner_hasn_id == 'h_owner_1'
        return [
            SimpleNamespace(hasn_id='a_1', display_name='一号 Agent', agent_name='agent_one'),
            SimpleNamespace(hasn_id='a_2', display_name='', agent_name='agent_two'),
        ]

    monkeypatch.setattr(onboarding_service_module, 'create_refresh_token', fake_refresh_token_creator)
    monkeypatch.setattr(onboarding_service_module.hasn_humans_dao, 'get_by_user_id', fake_get_human)
    monkeypatch.setattr(onboarding_service_module.hasn_agents_dao, 'get_active_agents_by_owner', fake_get_active_agents)

    service = HasnPhoneAuthService(
        redis=redis,
        sms=FakeSms(),
        users=users,
        token_expire_seconds=86400,
        token_creator=fake_token_creator,
        llm_credentials=FakeLlmCredentialIssuer(),
        agent_tokens=FakeAgentTokenIssuer(),
    )

    response = await service.verify(db, PhoneVerifyRequest(phone='13800138000', code='654321'))

    assert [item.agent_hasn_id for item in response.agent_tokens] == ['a_1', 'a_2']
    assert response.agent_tokens[0].agent_name == '一号 Agent'
    assert response.agent_tokens[0].access_token == 'agent-token:a_1'
    assert response.agent_tokens[0].scopes == ['message.read', 'knowledge.read']
    assert response.agent_tokens[0].expire_time == '2026-05-18T00:00:00+00:00'
    assert response.agent_tokens[0].expires_at_unix == 1779062400
    assert response.agent_tokens[1].agent_name == 'agent_two'
    assert response.agent_tokens[1].access_token == 'agent-token:a_2'


@dataclass
class FakeOnboardingGateway:
    user: FakeUser = field(default_factory=lambda: FakeUser(7, 'old-user', '老用户', '13800138000'))
    consumed: list[tuple[str, str, str]] = field(default_factory=list)
    node_infos: list[dict[str, Any]] = field(default_factory=list)

    async def get_user(self, db: Any, user_id: int) -> FakeUser | None:
        return self.user if user_id == self.user.id else None

    async def ensure_human(self, db: Any, user: FakeUser) -> tuple[Any, bool]:
        return SimpleNamespace(hasn_id='h_owner_1', name=user.nickname), False

    async def ensure_node(self, db: Any, user_id: int, owner_id: str, request: OnboardingEnsureRequest) -> Any:
        node_info = {
            'device_fingerprint': request.node.node_id,
            'device_platform': request.node.platform,
            'client_version': request.node.client_version,
            'protocol': request.client.protocol,
            'supported_extensions': request.client.supported_extensions or [],
        }
        self.node_infos.append(node_info)
        return SimpleNamespace(node_id=request.node.node_id)

    async def ensure_owner_binding(self, db: Any, node_id: str, owner_id: str) -> Any:
        return SimpleNamespace(node_id=node_id, owner_id=owner_id, status='active', sync_revision=3)

    async def ensure_default_agent(self, db: Any, owner_id: str, node_id: str | None) -> tuple[Any, bool]:
        return SimpleNamespace(hasn_id='a_default_1', owner_id=owner_id, name=DEFAULT_AGENT_DISPLAY_NAME), True

    async def consume_pending_intent(self, db: Any, pending_intent_id: str, owner_id: str, agent_hasn_id: str) -> bool:
        self.consumed.append((pending_intent_id, owner_id, agent_hasn_id))
        return True

    async def get_sandbox_summary(self, db: Any, owner_id: str) -> SandboxSummary | None:
        return SandboxSummary(sandbox_id='sb_owner_1', status='sleeping', base_url=None)

    async def get_sync_feed_head(self, db: Any, owner_id: str) -> int:
        # B2②：模拟该 owner 权威 feed 已推进到 revision 42（非 0，锁死不再硬编码 0）。
        assert owner_id == 'h_owner_1'
        return 42


@pytest.mark.asyncio
async def test_onboarding_ensure_closes_old_user_default_agent_and_pending_intent_loop() -> None:
    gateway = FakeOnboardingGateway()
    service = HasnOnboardingService(gateway=gateway, agent_tokens=FakeAgentTokenIssuer())
    request = OnboardingEnsureRequest(
        node=NodeClaim(
            node_id='n_device_1',
            device_name='MacBook',
            platform='macos',
            client_version='1.0.0',
        ),
        client=ClientInfo(protocol='hasn/0.2', supported_extensions=['sync.pull']),
        pending_intent_id='pi_resume_1',
    )

    response = await service.ensure(db=None, user_id=7, request=request)

    assert response.human.owner_id == 'h_owner_1'
    assert response.owner_binding.node_id == 'n_device_1'
    assert response.owner_binding.revision == 3
    assert response.default_agent.hasn_id == 'a_default_1'
    assert response.default_agent.display_name == DEFAULT_AGENT_DISPLAY_NAME
    assert response.default_agent.access_token == 'agent-token:a_default_1'
    assert response.default_agent.scopes == ['message.read', 'knowledge.read']
    # 沙箱已退役（CLEAN-3·hasn_tenant_sandboxes 恒 None），字段仅兼容 daemon 保留。
    assert response.sandbox is None
    # B2②：bootstrap 游标必须是 gateway 给出的权威 feed 真实 head（42），不再硬编码 0。
    assert response.sync_cursor == 'owner:h_owner_1:42'
    assert gateway.consumed == [('pi_resume_1', 'h_owner_1', 'a_default_1')]
    assert set(gateway.node_infos[0]).isdisjoint(PRIVATE_NODE_INFO_KEYS)


@pytest.mark.asyncio
async def test_llm_credential_issuer_never_sends_global_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 LLM 凭据下发只给 token+base_url，主模型恒为 None——绝不下发全局默认模型。

    回归锁：历史上 ``issue()`` 回 ``settings.LLM_DEFAULT_MODEL``（恒 'gpt-5.5'），会被 daemon
    镜像进 ``owner_llm_credentials.llm_model`` 并在主模型解析链里以 ``owner_model`` 身份恒先命中，
    把平台默认配置下发（PDC）的 ``agent_runtime.models.main`` 永久架空（运营改了也下不去）。
    全局默认模型唯一权威是 PDC 单行 + 节点 config 兜底；此处必须留空。
    """
    from backend.app.hasn.service.hasn_onboarding_service import SqlAlchemyLlmCredentialIssuer
    from backend.app.newapi.service import llm_newapi_user_mapping_service

    async def _fake_ensure_newapi_user(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(newapi_token_key='tok_xyz')

    # 隔离外部 new-api 依赖（测试的是 issuer 的「选模型」决策，不是 new-api 本身）。
    monkeypatch.setattr(
        llm_newapi_user_mapping_service, 'ensure_newapi_user', _fake_ensure_newapi_user
    )

    user = FakeUser(id=42, username='13800000000', nickname='测试主人', phone='13800000000')
    token, base_url, model = await SqlAlchemyLlmCredentialIssuer().issue(db=None, user=user)

    assert token == 'sk-tok_xyz'
    assert base_url  # per-owner base_url 仍下发
    assert model is None  # 关键断言：主模型留空，平台默认当权威
