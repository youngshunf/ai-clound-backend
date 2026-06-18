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
