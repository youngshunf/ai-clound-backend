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
