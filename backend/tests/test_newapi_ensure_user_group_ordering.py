"""单测：ensure_newapi_user 设默认分组的「调用顺序不变量」。

生产实测根因：new-api UpdateUser 是 GET 用户整对象→改字段→PUT 整对象回写。
set_user_quota 与 bootstrap_user_access_token（内含 set_user_password）都是这种整对象回写，
若它们的 GET 读到「设组前的旧快照」，PUT 会把刚设好的分组覆盖回空字符串 → 新用户首次
relay 即报「No available channel under group ''」。

修复：ensure_user_group 必须排在所有「用户级 GET→PUT」之后（quota / access_token 之后、
建 relay token 之前——provision_user_relay_token 只建 token 不 PUT 用户，不会再覆盖）。

本测试不连任何真实 new-api / DB，纯验证 create 路径的方法调用顺序，以及 existing 路径的
分组自愈调用。
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
    """create 路径：ensure_user_group 必须排在 set_user_quota 与 bootstrap 之后、建 token 之前。"""
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
    # 核心不变量：设组在两次「用户级 GET→PUT」之后，否则会被它们的整对象回写覆盖回空组。
    i_group = calls.index('ensure_user_group')
    assert i_group > calls.index('set_user_quota'), f'设组必须排在 set_user_quota 之后，实际顺序={calls}'
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
