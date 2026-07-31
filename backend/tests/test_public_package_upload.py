"""GB 级制品包（引擎包 / 模型包）分片上传契约。

不能走单次预签名 PUT：`write_stream` 的超时与预签名 TTL 都硬顶 1800 秒且不可续传，
2 GiB 级的包只要吞吐略低就会在最后一刻整体作废。这里守住分片路径与幂等语义。
"""

from __future__ import annotations

import io

from typing import Any

import pytest

from backend.common.exception import errors
from backend.plugin.s3.model import S3Storage
from backend.plugin.s3.utils import file_ops


def _storage(*, endpoint: str = 'https://s3.cn-east-1.qiniucs.com', prefix: str = 'pub') -> S3Storage:
    storage = S3Storage(
        name='test',
        endpoint=endpoint,
        access_key='ak',
        secret_key='sk',
        bucket='hasn-pub',
        access='public',
        sign_strategy='none',
        prefix=prefix,
        region='cn-east-1',
        cdn_domain='https://cdn.example.com',
        remark='',
    )
    storage.id = 1
    return storage


def _fake_auth(*args: Any, **kwargs: Any) -> Any:
    """模拟七牛 Auth，只需要能签出一个上传凭证。"""
    return type('_Auth', (), {'upload_token': lambda *a, **k: 'tok'})()


class _Info:
    """模拟七牛 SDK 的 ResponseInfo。"""

    def __init__(self, status_code: int, text_body: str = '') -> None:
        self.status_code = status_code
        self.text_body = text_body
        self.error = ''


def test_package_upload_timeout_scales_past_the_half_hour_ceiling() -> None:
    """4 GiB 包必须拿到远超 1800 秒的预算，否则单次上传注定超时作废。"""
    assert file_ops._package_upload_timeout(1024) == pytest.approx(1800.0)
    four_gib = 4 * 1024 * 1024 * 1024
    assert file_ops._package_upload_timeout(four_gib) > 1800.0
    # 按 100 KiB/s 保守下限推算：4 GiB 约 11.9 小时。
    assert file_ops._package_upload_timeout(four_gib) == pytest.approx(four_gib / (100 * 1024))


@pytest.mark.asyncio
async def test_large_package_uses_chunked_upload_not_single_put(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过分片阈值的包必须走 put_stream_v2，且分片大小为 4 MiB。"""
    captured: dict[str, Any] = {}

    def fake_put_stream_v2(token, key, file, fname, size, **kwargs):  # noqa: ANN001, ANN202
        captured['key'] = key
        captured['size'] = size
        captured['part_size'] = kwargs.get('part_size')
        return {'key': key, 'hash': 'etag', 'size': size}, _Info(200)

    def fail_put_data(*args: Any, **kwargs: Any) -> None:
        raise AssertionError('大包不得走单次 put_data')

    monkeypatch.setattr(file_ops, 'put_stream_v2', fake_put_stream_v2)
    monkeypatch.setattr(file_ops, 'put_data', fail_put_data)
    monkeypatch.setattr(file_ops, 'Auth', _fake_auth)

    size = file_ops.PUBLIC_PACKAGE_PART_BYTES * 3
    await file_ops.write_public_package_stream(
        _storage(),
        'runtime-model/imagelab/u2netp/2024.07/abc-u2netp.zip',
        io.BytesIO(b'x' * size),
        size=size,
        content_type='application/zip',
    )

    assert captured['key'] == 'pub/runtime-model/imagelab/u2netp/2024.07/abc-u2netp.zip'
    assert captured['size'] == size
    assert captured['part_size'] == file_ops.PUBLIC_PACKAGE_PART_BYTES


@pytest.mark.asyncio
async def test_repeated_stage_of_identical_package_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """object key 内嵌内容摘要，重复 stage 命中 insertOnly 冲突时按幂等成功处理。"""

    def fake_put_stream_v2(*args: Any, **kwargs: Any) -> tuple[None, _Info]:
        return None, _Info(file_ops._QINIU_FILE_EXISTS_STATUS, 'file exists')

    monkeypatch.setattr(file_ops, 'put_stream_v2', fake_put_stream_v2)
    monkeypatch.setattr(file_ops, 'Auth', _fake_auth)

    size = file_ops.PUBLIC_PACKAGE_PART_BYTES * 2
    # 不抛异常即为通过：重复发布同一个包不应让运维以为上传失败。
    await file_ops.write_public_package_stream(
        _storage(),
        'runtime-model/imagelab/u2netp/2024.07/abc-u2netp.zip',
        io.BytesIO(b'x' * size),
        size=size,
        content_type='application/zip',
    )


@pytest.mark.asyncio
async def test_upload_result_mismatching_size_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 回报的 key/大小与请求不一致时必须失败，不能让残缺对象被签进目录。"""

    def fake_put_stream_v2(token, key, file, fname, size, **kwargs):  # noqa: ANN001, ANN202
        return {'key': key, 'hash': 'etag', 'size': size - 1}, _Info(200)

    monkeypatch.setattr(file_ops, 'put_stream_v2', fake_put_stream_v2)
    monkeypatch.setattr(file_ops, 'Auth', _fake_auth)

    size = file_ops.PUBLIC_PACKAGE_PART_BYTES * 2
    with pytest.raises(errors.ServerError, match='key/大小不一致'):
        await file_ops.write_public_package_stream(
            _storage(),
            'runtime-model/imagelab/u2netp/2024.07/abc-u2netp.zip',
            io.BytesIO(b'x' * size),
            size=size,
            content_type='application/zip',
        )


@pytest.mark.asyncio
async def test_non_qiniu_provider_falls_back_with_raised_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """非七牛 provider 退回预签名 PUT，但必须解除 1800 秒硬顶。"""
    captured: dict[str, Any] = {}

    async def fake_write_stream(  # noqa: ANN202
        storage,  # noqa: ANN001
        path,  # noqa: ANN001
        contents,  # noqa: ANN001
        *,
        size: int,
        content_type: str | None = None,
        timeout_ceiling: float = 1800.0,
    ):
        captured['timeout_ceiling'] = timeout_ceiling
        captured['size'] = size
        async for _ in contents:
            pass

    monkeypatch.setattr(file_ops, 'write_stream', fake_write_stream)

    size = 2 * 1024 * 1024 * 1024
    await file_ops.write_public_package_stream(
        _storage(endpoint='https://s3.amazonaws.com'),
        'runtime-model/imagelab/u2netp/2024.07/abc-u2netp.zip',
        io.BytesIO(b''),
        size=size,
        content_type='application/zip',
    )

    assert captured['size'] == size
    assert captured['timeout_ceiling'] > 1800.0


@pytest.mark.asyncio
async def test_package_upload_refuses_immutable_speech_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """制品包通道不得写进语音包的内容寻址命名空间。"""
    with pytest.raises(errors.RequestError, match='不可变语音包命名空间'):
        await file_ops.write_public_package_stream(
            _storage(),
            f'{file_ops.IMMUTABLE_SPEECH_PACKAGE_PREFIX}deadbeef/pkg.zip',
            io.BytesIO(b'x'),
            size=1,
            content_type='application/zip',
        )
