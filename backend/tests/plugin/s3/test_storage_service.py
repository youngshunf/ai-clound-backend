from __future__ import annotations

import hashlib

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.common.exception import errors
from backend.plugin.s3.service import storage_service as svc_mod
from backend.plugin.s3.service.storage_service import StorageService


def _storage(**overrides: object) -> SimpleNamespace:
    data = {
        'id': 1,
        'endpoint': 'https://oss.example.com',
        'access_key': 'ak',
        'secret_key': 'sk',
        'bucket': 'private-bucket',
        'prefix': 'huanxing',
        'region': 'cn-test',
        'cdn_domain': 'https://cdn.example.com',
        'access': 'private',
        'sign_strategy': 's3_presign',
        'remark': None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_category_policy_public_vs_private() -> None:
    assert svc_mod.CATEGORY_POLICY['user_avatar'][0] == 'public'
    assert svc_mod.CATEGORY_POLICY['post_image'][0] == 'public'
    assert svc_mod.CATEGORY_POLICY['dm_attachment'] == ('private', 3600)
    assert svc_mod.CATEGORY_POLICY['private_doc'][0] == 'private'


def test_pick_storage_selects_by_access() -> None:
    pub = _storage(id=1, access='public')
    priv = _storage(id=2, access='private')
    assert svc_mod._pick_storage([pub, priv], 'private').id == 2
    assert svc_mod._pick_storage([pub, priv], 'public').id == 1
    with pytest.raises(errors.ServerError):
        svc_mod._pick_storage([pub], 'private')


def test_build_key_is_content_addressed() -> None:
    data = b'hello-world'
    key = svc_mod._build_key('dm_attachment', filename='photo.JPG', data=data)
    digest = hashlib.md5(data).hexdigest()[:12]
    assert key.startswith('dm/')
    assert key.endswith(f'{digest}.jpg')  # ext 小写、content-addressed


@pytest.mark.asyncio
async def test_upload_routes_to_private_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    storages = [_storage(id=1, access='public'), _storage(id=2, access='private')]
    monkeypatch.setattr(svc_mod.s3_storage_dao, 'get_all', AsyncMock(return_value=storages))
    write_spy = AsyncMock()
    monkeypatch.setattr(svc_mod, 'write_bytes', write_spy)

    data = b'attachment-bytes'
    ref = await StorageService.upload(
        db=object(), data=data, category='dm_attachment', filename='a.png', content_type='image/png'
    )

    assert ref.access == 'private'
    assert ref.storage_id == 2  # 选中 private 桶
    assert ref.mime == 'image/png'
    assert ref.size == len(data)
    # 稳定 URL 由 cdn_domain + prefix + key 现拼（不入库；07 D8）
    assert ref.stable_url == f'https://cdn.example.com/huanxing/{ref.object_key}'
    write_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_signed_url_dispatches_s3_presign(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _storage(id=2, access='private', sign_strategy='s3_presign')
    monkeypatch.setattr(svc_mod.s3_storage_dao, 'get', AsyncMock(return_value=storage))
    monkeypatch.setattr(
        svc_mod,
        'presign_read_key',
        AsyncMock(return_value='https://oss.example.com/private-bucket/huanxing/dm/x.png?sig=abc'),
    )
    url = await StorageService.signed_url(db=object(), storage_id=2, object_key='dm/x.png', expires_in=600)
    assert url.endswith('?sig=abc')


def test_cdn_timestamp_signing_algorithm() -> None:
    """七牛时间戳防盗链：sign=md5(key + encoded_path + t_hex)，纯算法真实校验。"""
    storage = _storage(sign_strategy='cdn_timestamp', remark='{"cdn_sign_key": "secret-k"}')
    url = StorageService._cdn_timestamp_url(storage, 'dm/x.png', 600)
    assert url.startswith('https://cdn.example.com/huanxing/dm/x.png?sign=')
    assert '&t=' in url
    # 反算 sign 校验算法正确
    t_hex = url.split('&t=')[1]
    sign = url.split('sign=')[1].split('&')[0]
    expected = hashlib.md5(f'secret-k/huanxing/dm/x.png{t_hex}'.encode()).hexdigest()
    assert sign == expected


def test_cdn_timestamp_missing_key_raises() -> None:
    """零 fake：cdn_timestamp 缺密钥时抛错，不静默降级。"""
    storage = _storage(sign_strategy='cdn_timestamp', remark=None)
    with pytest.raises(errors.ServerError):
        StorageService._cdn_timestamp_url(storage, 'dm/x.png', 600)


def test_qiniu_private_signing_algorithm() -> None:
    """七牛私有下载凭证：token=AK:urlsafe_b64(hmac_sha1(SK, '<url>?e=deadline'))，纯算法真实校验。"""
    import base64 as _b64
    import hmac as _hmac

    storage = _storage(sign_strategy='qiniu_private', access_key='AK', secret_key='SK')
    url = StorageService._qiniu_private_url(storage, 'dm/x.png', 600)
    assert url.startswith('https://cdn.example.com/huanxing/dm/x.png?e=')
    assert '&token=AK:' in url
    # 反算 token 校验：签名串就是 &token= 之前的整段（含 ?e=deadline）
    to_sign, token_part = url.split('&token=')
    ak, enc = token_part.split(':', 1)
    assert ak == 'AK'
    expected = _b64.urlsafe_b64encode(_hmac.new(b'SK', to_sign.encode(), hashlib.sha1).digest()).decode()
    assert enc == expected


def test_qiniu_private_missing_creds_raises() -> None:
    """零 fake：qiniu_private 缺 cdn_domain / AK / SK 时抛错，不伪造可读 URL。"""
    with pytest.raises(errors.ServerError):
        StorageService._qiniu_private_url(_storage(sign_strategy='qiniu_private', cdn_domain=None), 'dm/x.png', 600)
    with pytest.raises(errors.ServerError):
        StorageService._qiniu_private_url(_storage(sign_strategy='qiniu_private', secret_key=''), 'dm/x.png', 600)


@pytest.mark.asyncio
async def test_unknown_sign_strategy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _storage(sign_strategy='bogus')
    monkeypatch.setattr(svc_mod.s3_storage_dao, 'get', AsyncMock(return_value=storage))
    with pytest.raises(errors.ServerError):
        await StorageService.signed_url(db=object(), storage_id=2, object_key='dm/x.png')


class _FakeRedis:
    """内存 fake，只覆盖 get/setex/mget 三个缓存边界方法。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]


@pytest.mark.asyncio
async def test_signed_url_cached_miss_then_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(svc_mod, 'redis_client', fake)
    sign_spy = AsyncMock(return_value='https://signed/url?sig=1')
    monkeypatch.setattr(StorageService, 'signed_url', classmethod(lambda cls, db, **kw: sign_spy(**kw)))

    # 未命中：live 签名 + SETEX(ttl = expires_in - margin)
    url1 = await StorageService.signed_url_cached(db=object(), storage_id=2, object_key='dm/x.png', expires_in=600)
    assert url1 == 'https://signed/url?sig=1'
    assert sign_spy.await_count == 1
    assert fake.setex_calls[0][1] == 600 - svc_mod.SIGN_CACHE_MARGIN_SECONDS  # margin 生效

    # 命中：不再签名
    url2 = await StorageService.signed_url_cached(db=object(), storage_id=2, object_key='dm/x.png', expires_in=600)
    assert url2 == 'https://signed/url?sig=1'
    assert sign_spy.await_count == 1  # 仍是 1，命中缓存


@pytest.mark.asyncio
async def test_signed_urls_cached_batch_only_signs_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    fake.store[StorageService._cache_key(2, 'dm/a.png')] = 'cached-a'  # 预置一个命中
    monkeypatch.setattr(svc_mod, 'redis_client', fake)
    monkeypatch.setattr(svc_mod.s3_storage_dao, 'get', AsyncMock(return_value=_storage(id=2, access='private')))
    monkeypatch.setattr(svc_mod, 'presign_read_key', AsyncMock(return_value='fresh-signed'))

    items = [(2, 'dm/a.png'), (2, 'dm/b.png')]
    result = await StorageService.signed_urls_cached(db=object(), items=items, expires_in=600)
    assert result[2, 'dm/a.png'] == 'cached-a'  # 命中
    assert result[2, 'dm/b.png'] == 'fresh-signed'  # 未命中→签名
    # 仅未命中的 b 触发 setex
    assert [c[0] for c in fake.setex_calls] == [StorageService._cache_key(2, 'dm/b.png')]
