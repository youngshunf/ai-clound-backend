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
    info = await svc.ensure_newapi_user(db, 900117, username='13800138000', nickname='生产恢复用户')

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
    client.bootstrap_user_access_token.assert_awaited_once_with(newapi_user_id=117, username='13800138000')
    client.provision_user_relay_token.assert_awaited_once_with(
        newapi_user_id=117,
        username='13800138000',
        user_access_token='user-access-token',
        name='huanxing 默认 Key',
    )


# ========== 自愈：清库保留 new-api 数据后两边 key 漂移的对账 ==========


class _FakeMapping:
    """_reconcile_mapping_key 只做属性读写 + db.flush，用轻量替身即可。"""

    def __init__(self, **kw: object) -> None:
        self.huanxing_user_id = kw.get('huanxing_user_id', 900117)
        self.newapi_user_id = kw.get('newapi_user_id', 117)
        self.newapi_token_id = kw.get('newapi_token_id', 601)
        self.newapi_token_key = kw.get('newapi_token_key', 'stale-key')
        self.app_code = kw.get('app_code', 'huanxing')
        self.status = kw.get('status', 'active')
        self.newapi_access_token = kw.get('newapi_access_token')


def _client() -> NewApiAdminClient:
    return NewApiAdminClient(base_url='http://newapi.local/api', access_token='admin-token', admin_user_id=1)


async def test_reconcile_mapping_key_aligns_drifted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """token 仍有效但本地缓存 key 与 new-api 权威漂移 → 就地对齐为权威 key。"""
    client = _client()
    client.get_token = AsyncMock(return_value={'id': 601, 'status': 1})  # type: ignore[method-assign]
    client.get_token_key = AsyncMock(return_value='authoritative-key')  # type: ignore[method-assign]
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)

    db = _FakeDb()
    m = _FakeMapping(newapi_token_id=601, newapi_token_key='stale-key')
    key = await svc._reconcile_mapping_key(db, m)  # type: ignore[arg-type]

    assert key == 'authoritative-key'
    assert m.newapi_token_key == 'authoritative-key'
    assert db.flush_count == 1


async def test_reconcile_mapping_key_rebuilds_when_token_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """token 在 new-api 确定不存在 → 以用户身份 find-or-create 重建并写回新 id+key。"""
    client = _client()
    client.get_token = AsyncMock(  # type: ignore[method-assign]
        side_effect=NewApiError('new-api GET /admin_token/601 success=false: "token不存在"', status_code=200)
    )
    client.get_user = AsyncMock(return_value={'id': 117, 'username': '13800138000'})  # type: ignore[method-assign]
    client.provision_user_relay_token = AsyncMock(return_value=(701, 'new-key'))  # type: ignore[method-assign]
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)

    db = _FakeDb()
    m = _FakeMapping(
        newapi_token_id=601,
        newapi_token_key='dead-key',
        newapi_access_token=key_encryption.encrypt('user-access-token'),
    )
    key = await svc._reconcile_mapping_key(db, m)  # type: ignore[arg-type]

    assert key == 'new-key'
    assert m.newapi_token_id == 701
    assert m.newapi_token_key == 'new-key'
    client.provision_user_relay_token.assert_awaited_once()


async def test_reconcile_mapping_key_keeps_cache_when_newapi_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """new-api 传输层不可达（status_code=None）→ 保守返回缓存 key，不重建、不破坏登录。"""
    client = _client()
    client.get_token = AsyncMock(  # type: ignore[method-assign]
        side_effect=NewApiError('new-api 不可达: timeout', status_code=None)
    )
    client.provision_user_relay_token = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)

    db = _FakeDb()
    m = _FakeMapping(newapi_token_key='cached-key')
    key = await svc._reconcile_mapping_key(db, m)  # type: ignore[arg-type]

    assert key == 'cached-key'
    assert db.flush_count == 0
    client.provision_user_relay_token.assert_not_awaited()


async def test_newapi_token_active_present_active(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.get_token = AsyncMock(return_value={'id': 9, 'status': 1})  # type: ignore[method-assign]
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)
    assert await svc._newapi_token_active(9) is True


async def test_newapi_token_active_present_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    client.get_token = AsyncMock(return_value={'id': 9, 'status': 2})  # type: ignore[method-assign]
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)
    assert await svc._newapi_token_active(9) is False


async def test_newapi_token_active_gone_vs_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """确定应答=失效(False)；传输不可达=保守视为有效(True)，避免抖动时误旋转。"""
    client = _client()
    monkeypatch.setattr(service_module, 'newapi_admin_client', client)

    client.get_token = AsyncMock(side_effect=NewApiError('not found', status_code=200))  # type: ignore[method-assign]
    assert await svc._newapi_token_active(9) is False

    client.get_token = AsyncMock(side_effect=NewApiError('unreachable', status_code=None))  # type: ignore[method-assign]
    assert await svc._newapi_token_active(9) is True
