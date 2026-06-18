from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.app.newapi.client import NewApiAdminClient, NewApiError

pytestmark = pytest.mark.asyncio


async def test_ensure_user_reuses_user_when_create_hits_existing_username() -> None:
    """search/create 竞态或孤儿用户存在时，应二次查询并复用已有 new-api 用户."""
    client = NewApiAdminClient(
        base_url='http://newapi.local/api',
        access_token='admin-token',
        admin_user_id=1,
    )
    client.search_user_by_username = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            None,
            {'id': 42, 'username': '13800138000'},
        ]
    )
    client.create_user = AsyncMock(  # type: ignore[method-assign]
        side_effect=NewApiError(
            'new-api POST /user/ success=false: "duplicate key value violates unique constraint users_username_key"',
            endpoint='/user/',
        )
    )

    assert await client.ensure_user(username='13800138000', display_name='138****8000') == 42
    assert client.search_user_by_username.await_count == 2


async def test_provision_user_relay_token_reuses_existing_token_before_add() -> None:
    """本地映射被清理但 new-api token 仍存在时，应直接复用历史 token，避免重复 AddToken 500."""
    client = NewApiAdminClient(
        base_url='http://newapi.local/api',
        access_token='admin-token',
        admin_user_id=1,
    )
    client.find_token = AsyncMock(return_value={'id': 601, 'name': 'huanxing 默认 Key'})  # type: ignore[method-assign]
    client.get_token_key = AsyncMock(return_value='existing-token-key')  # type: ignore[method-assign]
    client.add_token = AsyncMock(  # type: ignore[method-assign]
        side_effect=NewApiError(
            'new-api POST /token/ success=false: "token name already exists"',
            endpoint='/token/',
        )
    )

    token_id, key = await client.provision_user_relay_token(
        newapi_user_id=117,
        username='13800138000',
        user_access_token='user-access-token',
        name='huanxing 默认 Key',
    )

    assert token_id == 601
    assert key == 'existing-token-key'
    client.find_token.assert_awaited_once_with(username='13800138000', name='huanxing 默认 Key')
    client.get_token_key.assert_awaited_once_with(601)
    client.add_token.assert_not_awaited()


def _client() -> NewApiAdminClient:
    return NewApiAdminClient(base_url='http://newapi.local/api', access_token='admin-token', admin_user_id=1)


async def test_ensure_user_group_sets_group_when_empty() -> None:
    """空组用户（API 化创建未带 group）应被整对象回 PUT 修正为目标分组，relay 才能匹配渠道."""
    client = _client()
    client.get_user = AsyncMock(  # type: ignore[method-assign]
        return_value={'id': 8, 'username': '18687200686', 'group': '', 'quota': 500000}
    )
    client._request = AsyncMock(return_value=None)  # type: ignore[method-assign]

    changed = await client.ensure_user_group(newapi_user_id=8, group='default')

    assert changed is True
    method, path = client._request.await_args.args
    payload = client._request.await_args.kwargs['json']
    assert (method, path) == ('PUT', '/user/')
    assert payload['group'] == 'default'
    assert payload['quota'] == 500000  # 整对象回写，不丢额度


async def test_ensure_user_group_noop_when_already_correct() -> None:
    """分组已正确 → 不发 PUT（幂等），返回 False."""
    client = _client()
    client.get_user = AsyncMock(return_value={'id': 8, 'group': 'default'})  # type: ignore[method-assign]
    client._request = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await client.ensure_user_group(newapi_user_id=8, group='default') is False
    client._request.assert_not_awaited()


async def test_ensure_user_group_noop_when_target_empty() -> None:
    """目标分组为空字符串 → 不强制（沿用 new-api 行为），不触碰用户."""
    client = _client()
    client.get_user = AsyncMock()  # type: ignore[method-assign]

    assert await client.ensure_user_group(newapi_user_id=8, group='') is False
    client.get_user.assert_not_awaited()
