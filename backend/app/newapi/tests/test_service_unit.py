from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.app.newapi import service as service_module
from backend.app.newapi.client import NewApiAdminClient, NewApiError
from backend.app.newapi.service import llm_newapi_user_mapping_service as svc
from backend.common.security.encryption import key_encryption

pytestmark = pytest.mark.asyncio


class _NoMappingDao:
    async def get_by_user(self, db: object, huanxing_user_id: int, app_code: str = 'huanxing') -> None:
        return None


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1


async def test_ensure_newapi_user_recovers_orphan_newapi_user_after_local_mapping_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地映射被清理但 new-api 用户仍存在时，登录补映射不应因用户名冲突 500."""
    client = NewApiAdminClient(
        base_url='http://newapi.local/api',
        access_token='admin-token',
        admin_user_id=1,
    )
    client.search_user_by_username = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            None,
            {'id': 117, 'username': '13800138000'},
        ]
    )
    client.create_user = AsyncMock(  # type: ignore[method-assign]
        side_effect=NewApiError(
            'new-api POST /user/ success=false: "duplicate key value violates unique constraint users_username_key"',
            endpoint='/user/',
        )
    )
    client.set_user_quota = AsyncMock()  # type: ignore[method-assign]
    client.bootstrap_user_access_token = AsyncMock(return_value='user-access-token')  # type: ignore[method-assign]
    client.provision_user_relay_token = AsyncMock(return_value=(601, 'relay-token-key'))  # type: ignore[method-assign]
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)
    monkeypatch.setattr(service_module, 'llm_newapi_user_mapping_dao', _NoMappingDao())

    db = _FakeDb()
    info = await svc.ensure_newapi_user(
        db, 900117, username='13800138000', nickname='生产恢复用户'
    )

    assert info.newapi_user_id == 117
    assert info.newapi_token_key == 'relay-token-key'
    assert info.status == 'active'
    assert db.flush_count == 1
    assert len(db.added) == 1

    mapping = db.added[0]
    assert mapping.huanxing_user_id == 900117
    assert mapping.newapi_user_id == 117
    assert mapping.newapi_token_id == 601
    assert key_encryption.decrypt(mapping.newapi_access_token) == 'user-access-token'
    assert client.search_user_by_username.await_count == 2
    client.set_user_quota.assert_awaited_once()
    client.bootstrap_user_access_token.assert_awaited_once_with(
        newapi_user_id=117, username='13800138000'
    )
    client.provision_user_relay_token.assert_awaited_once_with(
        newapi_user_id=117,
        username='13800138000',
        user_access_token='user-access-token',
        name='huanxing 默认 Key',
    )
