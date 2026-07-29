"""单测：ensure_newapi_user / ensure_agent_token 的「用户分组不被清空」不变量。

生产实测根因（菌子 primary gpt-5.5 全天 403「分组已删除」）：
- client.set_user_password（bootstrap access_token 内部调用）早期是「只带 id/username/password 的
  部分字段 PUT」，new-api UpdateUser 把 body 反序列化成完整 User struct、缺失字段取 Go 零值 →
  **确定性地把 group 清成空字符串**。
- 铸/重签 Agent relay token 时（ensure_agent_token），_ensure_user_access_token 在 access_token
  缓存缺失/解密失败时走 bootstrap → set_user_password → 清空父 user 分组；且该路径原本没有
  ensure_user_group 兜回（步骤1 ensure_newapi_user 的设组发生在此之前）→ 分身 relay token 空组
  → primary 报「No available channel / 分组已删除」，全天持续到主人下次登录才被自愈修回。

双重修复：
1. 根治：set_user_password 改为「GET 整对象 → 只改 password → 整对象回写」（对齐 ensure_user_group /
   set_user_quota），group/quota 原样保留、不再清空。
2. 防御纵深：ensure_agent_token 在 _ensure_user_access_token 之后补一次 ensure_user_group（幂等），
   把「铸 Agent token 后父 user 分组必为 default」变成不变量。
另需保持 create 路径「ensure_user_group 排在 bootstrap 的用户级 GET→PUT 之后」的不变量。
配额已改由幂等履约事件增量发放，建号路径不得再用绝对 quota 覆盖 NewAPI 权威余额。

本测试不连任何真实 new-api / DB，纯验证方法调用顺序与分组保留契约。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.newapi import service as svc
from backend.app.newapi.service import LlmNewapiUserMappingService

_FAKE_USER_ID = 6
_FAKE_TOKEN_ID = 15
_FAKE_RAW_KEY = 'rawkey0000000000000000000000000000000000000000'


def _ordered_client(calls: list[str]) -> MagicMock:
    """构造记录调用顺序的 newapi_admin_client 替身。"""

    def _rec(name: str, ret: object) -> AsyncMock:
        # 同步 side_effect 即可：AsyncMock 调用它并把返回值作为 await 结果（无需 async）。
        def _inner(*_a: object, **_kw: object) -> object:
            calls.append(name)
            return ret

        return AsyncMock(side_effect=_inner)

    client = MagicMock()
    client.ensure_user = _rec('ensure_user', _FAKE_USER_ID)
    client.set_user_quota = _rec('set_user_quota', None)
    client.bootstrap_user_access_token = _rec('bootstrap_user_access_token', 'access-token')
    client.ensure_user_group = _rec('ensure_user_group', True)
    client.provision_user_relay_token = _rec('provision_user_relay_token', (_FAKE_TOKEN_ID, _FAKE_RAW_KEY))
    return client


def _fake_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()  # AsyncSession.add 是同步
    db.flush = AsyncMock()  # flush 是异步
    return db


@pytest.mark.asyncio
async def test_create_path_sets_group_after_all_user_puts() -> None:
    """create 路径：不覆盖权威余额，且 ensure_user_group 排在 bootstrap 后、建 token 前。"""
    calls: list[str] = []
    client = _ordered_client(calls)
    with (
        patch.object(svc, 'newapi_admin_client', client),
        patch.object(svc.llm_newapi_user_mapping_dao, 'get_by_user', AsyncMock(return_value=None)),
        patch.object(svc.key_encryption, 'encrypt', MagicMock(return_value=b'enc')),
    ):
        info = await LlmNewapiUserMappingService.ensure_newapi_user(_fake_db(), 999, username='u_test')

    assert info.newapi_user_id == _FAKE_USER_ID
    assert info.newapi_token_key == _FAKE_RAW_KEY
    # 建号不得以绝对 quota 覆盖由 NewAPI 权威维护的余额。
    assert 'set_user_quota' not in calls, f'建号路径不得覆盖 NewAPI 权威余额，实际顺序={calls}'
    # 核心不变量：设组在 bootstrap 的「用户级 GET→PUT」之后，否则会被整对象回写覆盖回空组。
    i_group = calls.index('ensure_user_group')
    assert i_group > calls.index('bootstrap_user_access_token'), f'设组必须排在 bootstrap 之后，实际顺序={calls}'
    # 设组后只建 relay token（不 PUT 用户），分组不再被覆盖。
    assert i_group < calls.index('provision_user_relay_token'), f'设组应在建 token 之前，实际顺序={calls}'


@pytest.mark.asyncio
async def test_existing_path_self_heals_group() -> None:
    """existing 路径：每次登录都对历史用户自愈分组（设组为空的历史用户下次登录被修回 default）。"""
    calls: list[str] = []
    client = _ordered_client(calls)
    existing = SimpleNamespace(
        huanxing_user_id=999,
        newapi_user_id=_FAKE_USER_ID,
        newapi_token_key=_FAKE_RAW_KEY,
        app_code='huanxing',
        status='active',
    )
    with (
        patch.object(svc, 'newapi_admin_client', client),
        patch.object(svc.llm_newapi_user_mapping_dao, 'get_by_user', AsyncMock(return_value=existing)),
        patch.object(LlmNewapiUserMappingService, '_reconcile_mapping_key', AsyncMock(return_value='valid-key')),
    ):
        info = await LlmNewapiUserMappingService.ensure_newapi_user(_fake_db(), 999)

    assert info.newapi_token_key == 'valid-key'
    client.ensure_user_group.assert_awaited_once()
    _args, kwargs = client.ensure_user_group.await_args
    assert kwargs['newapi_user_id'] == _FAKE_USER_ID
    assert kwargs['group'] == svc.settings.NEWAPI_DEFAULT_USER_GROUP


@pytest.mark.asyncio
async def test_set_user_password_preserves_group() -> None:
    """根治断言：set_user_password 整对象回写，保留 group（不再清成空字符串）。

    早期实现是「只带 id/username/password 的部分字段 PUT」→ new-api 零值覆盖 group='' → 生产 403 根因。
    修复后：GET 整对象 → 只改 password → PUT，group/quota 原样带回。
    """
    from backend.app.newapi.client import newapi_admin_client

    # new-api 侧当前用户：分组 vip、额度 100（GET 返回的整对象）
    current_user = {'id': 6, 'username': 'u_old', 'group': 'vip', 'quota': 100, 'display_name': '菌子'}
    captured: dict = {}

    async def _fake_get_user(uid: int) -> dict:
        return dict(current_user)

    async def _fake_request(method: str, path: str, **kw: object) -> dict:
        captured['method'] = method
        captured['path'] = path
        captured['json'] = kw.get('json')
        return {}

    with (
        patch.object(newapi_admin_client, 'get_user', AsyncMock(side_effect=_fake_get_user)),
        patch.object(newapi_admin_client, '_request', AsyncMock(side_effect=_fake_request)),
    ):
        await newapi_admin_client.set_user_password(newapi_user_id=6, username='u_new', password='tmp-pw-123')

    assert captured['method'] == 'PUT'
    payload = captured['json']
    # 核心不变量：group 原样保留，不被清空——这是菌子 primary 403 的根治点。
    assert payload['group'] == 'vip', f'set_user_password 不得清空分组，实际 payload={payload}'
    assert payload['quota'] == 100, 'quota 也应原样保留'
    # 密码/用户名按入参更新。
    assert payload['password'] == 'tmp-pw-123'
    assert payload['username'] == 'u_new'


@pytest.mark.asyncio
async def test_agent_token_reissue_reasserts_group() -> None:
    """防御纵深断言：铸/重签 Agent relay token（走重签路径）在建 token 前兜一次 ensure_user_group。

    模拟「已有 token 但在 new-api 已失效 → 重签」：_ensure_user_access_token 可能 bootstrap 清组，
    故必须在 provision_user_relay_token 之前 ensure_user_group（否则分身 relay token 空组 → primary 403）。
    """
    calls: list[str] = []
    client = _ordered_client(calls)
    mapping = SimpleNamespace(
        huanxing_user_id=999,
        newapi_user_id=_FAKE_USER_ID,
        newapi_token_key=_FAKE_RAW_KEY,
        newapi_access_token=b'enc',
        app_code='huanxing',
    )
    # 现有 Agent token 记录 → 但 new-api 侧已失效 → 走重签路径
    existing_token = SimpleNamespace(newapi_token_id=99, revoked_at=None)
    db_result = MagicMock()
    db_result.scalar_one_or_none = MagicMock(return_value=existing_token)
    db = _fake_db()
    db.execute = AsyncMock(return_value=db_result)

    mapping_info = SimpleNamespace(newapi_user_id=_FAKE_USER_ID)

    with (
        patch.object(svc, 'newapi_admin_client', client),
        patch.object(LlmNewapiUserMappingService, 'ensure_newapi_user', AsyncMock(return_value=mapping_info)),
        patch.object(LlmNewapiUserMappingService, '_newapi_token_active', AsyncMock(return_value=False)),
        patch.object(svc.llm_newapi_user_mapping_dao, 'get_by_user', AsyncMock(return_value=mapping)),
        patch.object(svc, 'resolve_newapi_username', AsyncMock(return_value='u_test')),
        patch.object(
            LlmNewapiUserMappingService, '_ensure_user_access_token', AsyncMock(return_value='access-token')
        ),
    ):
        await LlmNewapiUserMappingService.ensure_agent_token(db, 'agent_x', 999)

    # 重签路径必须在建 relay token 之前 ensure_user_group（兜回可能被 bootstrap 清空的分组）。
    assert 'ensure_user_group' in calls, f'重签路径缺少分组兜底，实际={calls}'
    assert calls.index('ensure_user_group') < calls.index('provision_user_relay_token'), (
        f'ensure_user_group 必须排在建 token 之前，实际={calls}'
    )
