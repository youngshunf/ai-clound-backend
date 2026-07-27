from __future__ import annotations

import hashlib

from types import SimpleNamespace, TracebackType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from backend.app.admin.api.v1.sys import user as user_api
from backend.app.hasn.api.v1.app import profile as profile_api

if TYPE_CHECKING:
    from typing_extensions import Self


def _storage(**overrides: Any) -> Any:
    data: dict[str, Any] = {
        'endpoint': 'https://oss.example.com',
        'access_key': 'ak',
        'secret_key': 'sk',
        'bucket': 'private-bucket',
        'prefix': 'assets',
        'region': 'cn-test',
        'cdn_domain': 'https://cdn.example.com',
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_operator_root_keeps_leading_slash_for_qiniu_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_operator(service: str, **kwargs: Any) -> object:
        captured.update(service=service, **kwargs)
        return object()

    from backend.plugin.s3.utils import file_ops

    monkeypatch.setattr(file_ops, 'AsyncOperator', fake_operator)

    operator = file_ops.get_operator(
        endpoint='http://s3.cn-south-1.qiniucs.com',
        access_key='ak',
        secret_key='sk',
        bucket='hasn',
        prefix='huanxing',
        region='any',
    )

    assert operator is not None
    assert captured['root'] == '/huanxing'


@pytest.mark.asyncio
async def test_upload_user_avatar_uses_normalized_root_and_returns_url_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _storage()
    content = b'avatar-bytes'
    presign_calls: list[tuple[str, int]] = []
    put_calls: list[tuple[str, bytes, dict]] = []
    captured: dict[str, str] = {}

    class FakePresignedRequest:
        url = 'https://oss.example.com/private-bucket/assets/avatars/u-123.png?sig=fresh'
        method = 'PUT'
        headers = {'x-test': '1'}

    class FakeOperator:
        async def presign_write(self, path: str, expire_second: int) -> FakePresignedRequest:
            presign_calls.append((path, expire_second))
            return FakePresignedRequest()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, *, timeout: int, trust_env: bool) -> None:
            assert timeout == 30
            assert trust_env is False

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            return None

        async def request(self, method: str, url: str, *, content: bytes, headers: dict) -> FakeResponse:
            put_calls.append((url, content, headers))
            assert method == 'PUT'
            return FakeResponse()

    def fake_operator(service: str, **kwargs: Any) -> FakeOperator:
        captured.update(service=service, **kwargs)
        return FakeOperator()

    monkeypatch.setattr(user_api.s3_storage_dao, 'get_all', AsyncMock(return_value=[storage]))
    monkeypatch.setattr(user_api, 'AsyncOperator', fake_operator, raising=False)

    try:
        from backend.plugin.s3.utils import file_ops

        monkeypatch.setattr(file_ops, 'AsyncOperator', fake_operator, raising=False)
        monkeypatch.setattr(file_ops.httpx, 'AsyncClient', FakeAsyncClient)
    except Exception:
        pass

    file: Any = SimpleNamespace(
        content_type='image/png',
        filename='profile.png',
        read=AsyncMock(return_value=content),
    )
    request: Any = SimpleNamespace(user=SimpleNamespace(uuid='u-123'))
    db: Any = object()

    response = await user_api.upload_user_avatar(db=db, request=request, file=file)

    digest = hashlib.md5(content).hexdigest()[:8]
    assert captured['root'] == '/assets'
    assert presign_calls == [(f'avatars/u-123_{digest}.png', 300)]
    assert put_calls == [
        (
            'https://oss.example.com/private-bucket/assets/avatars/u-123.png?sig=fresh',
            content,
            {'x-test': '1', 'Content-Type': 'image/png'},
        )
    ]
    assert response.data == {'url': f'https://cdn.example.com/assets/avatars/u-123_{digest}.png'}


@pytest.mark.asyncio
async def test_preset_avatars_return_cdn_storage_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_api.s3_storage_dao, 'get_all', AsyncMock(return_value=[_storage()]))
    db: Any = object()

    response = await profile_api.get_preset_avatars(db=db)

    assert response.data[0] == {
        'id': 'avatar-01',
        'url': 'https://cdn.example.com/assets/avatars/preset/avatar-01.png',
    }
    assert response.data[-1]['url'] == 'https://cdn.example.com/assets/avatars/preset/avatar-12.png'


@pytest.mark.asyncio
async def test_presign_read_url_strips_public_prefix_before_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.plugin.s3.utils import file_ops

    signed_calls: list[tuple[str, int]] = []

    class FakePresignedRequest:
        url = 'https://oss.example.com/private-bucket/assets/avatars/preset/avatar-01.png?sig=fresh'
        method = 'GET'
        headers = {'x-test': '1'}

    class FakeOperator:
        async def presign_read(self, path: str, expire_second: int) -> FakePresignedRequest:
            signed_calls.append((path, expire_second))
            return FakePresignedRequest()

    monkeypatch.setattr(file_ops, 'get_operator_for_storage', lambda storage: FakeOperator())

    response = await file_ops.presign_read_url(
        _storage(),
        'https://cdn.example.com/assets/avatars/preset/avatar-01.png',
        expires_in=600,
    )

    assert signed_calls == [('avatars/preset/avatar-01.png', 600)]
    assert response == {
        'url': 'https://oss.example.com/private-bucket/assets/avatars/preset/avatar-01.png?sig=fresh',
        'expires_in': 600,
        'source_url': 'https://cdn.example.com/assets/avatars/preset/avatar-01.png',
    }
