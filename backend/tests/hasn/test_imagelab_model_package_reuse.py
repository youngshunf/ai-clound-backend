"""图坊模型包 stage 的内容寻址幂等复用。

背景：模型包 key 由 `sha256` 派生，同一份字节永远落同一个对象。此前 stage 无条件重传，
817 MB 的 `birefnet-general` 在生产因服务器到对象存储那一段吞吐不足，超过 nginx 的
600 秒 `proxy_read_timeout` 返回 504——而 504 之后每一次重试又从零重传全部字节。

本测试钉死复用判据：只有**服务端流式复算**的桶内摘要与大小都一致才跳过重传；
任何不一致、探测异常一律回落到正常上传，绝不把未经证实的对象当成已发布内容。
"""

from __future__ import annotations

import pytest

from backend.app.hasn.service import app_catalog_service

_KEY = 'runtime-model/imagelab/birefnet-general/58f621f00f5d/0226cf2b19966b0c-birefnet-general.zip'
_SHA = '0226cf2b19966b0cad2d36dac1810943e71366a62ef8330c4f0259df2b6db575'
_SIZE = 817438838


class _Storage:
    """占位存储快照；本测试只关心复用判据，不触碰真实对象存储。"""


@pytest.mark.asyncio
async def test_reuses_object_when_server_recomputed_digest_matches(monkeypatch) -> None:
    async def _sha256(storage, *, object_key):  # noqa: ANN001, ANN202
        assert object_key == _KEY
        return _SHA, _SIZE

    monkeypatch.setattr(
        'backend.plugin.s3.service.storage_service.StorageService.sha256_on_storage',
        staticmethod(_sha256),
        raising=False,
    )
    monkeypatch.setattr(
        'backend.plugin.s3.utils.file_ops.build_object_url',
        lambda storage, path: f'https://cdn.example.com/{path}',
        raising=False,
    )

    url = await app_catalog_service._reuse_uploaded_model_package(
        _Storage(),
        object_key=_KEY,
        expected_sha256=_SHA,
        expected_size=_SIZE,
    )
    assert url == f'https://cdn.example.com/{_KEY}'


@pytest.mark.asyncio
async def test_does_not_reuse_when_digest_differs(monkeypatch) -> None:
    async def _sha256(storage, *, object_key):  # noqa: ANN001, ANN202
        return 'f' * 64, _SIZE

    monkeypatch.setattr(
        'backend.plugin.s3.service.storage_service.StorageService.sha256_on_storage',
        staticmethod(_sha256),
        raising=False,
    )

    url = await app_catalog_service._reuse_uploaded_model_package(
        _Storage(),
        object_key=_KEY,
        expected_sha256=_SHA,
        expected_size=_SIZE,
    )
    assert url is None


@pytest.mark.asyncio
async def test_does_not_reuse_when_size_differs(monkeypatch) -> None:
    """摘要相同但大小不同属于不可能状态，必须按不可复用处理而不是相信其中一个。"""

    async def _sha256(storage, *, object_key):  # noqa: ANN001, ANN202
        return _SHA, _SIZE - 1

    monkeypatch.setattr(
        'backend.plugin.s3.service.storage_service.StorageService.sha256_on_storage',
        staticmethod(_sha256),
        raising=False,
    )

    url = await app_catalog_service._reuse_uploaded_model_package(
        _Storage(),
        object_key=_KEY,
        expected_sha256=_SHA,
        expected_size=_SIZE,
    )
    assert url is None


@pytest.mark.asyncio
async def test_probe_failure_falls_back_to_upload(monkeypatch) -> None:
    """对象不存在或读取失败时回落到正常上传，不得因探测异常中断发布。"""

    async def _sha256(storage, *, object_key):  # noqa: ANN001, ANN202
        raise RuntimeError('对象不存在')

    monkeypatch.setattr(
        'backend.plugin.s3.service.storage_service.StorageService.sha256_on_storage',
        staticmethod(_sha256),
        raising=False,
    )

    url = await app_catalog_service._reuse_uploaded_model_package(
        _Storage(),
        object_key=_KEY,
        expected_sha256=_SHA,
        expected_size=_SIZE,
    )
    assert url is None
