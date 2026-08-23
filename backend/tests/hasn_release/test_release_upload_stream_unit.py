"""桌面端发布产物流式上传的回归契约。"""

import functools
import hashlib
import inspect

from collections.abc import AsyncIterator

import httpx
import pytest

from starlette.requests import Request

from backend.app.hasn_release.api.v1.ci.release import _required_content_length, ci_upload
from backend.app.hasn_release.schema.release import CiMultipartCompleteRequest, CiMultipartInitRequest
from backend.app.hasn_release.service import release_service as release_service_module
from backend.app.hasn_release.service.release_service import ReleaseService, _HashingSizedStream, _stage_release_upload
from backend.common.exception import errors


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:  # noqa: RUF029 - 构造真实异步字节流
    for value in values:
        yield value


@pytest.mark.asyncio
async def test_hashing_sized_stream_forwards_chunks_and_calculates_sha256() -> None:
    stream = _HashingSizedStream(_chunks(b'abc', b'', b'def'), 6)

    received = b''.join([chunk async for chunk in stream])

    assert received == b'abcdef'
    assert stream.received_size == 6
    assert stream.sha256 == hashlib.sha256(received).hexdigest()


@pytest.mark.asyncio
async def test_hashing_sized_stream_rejects_size_mismatch() -> None:
    stream = _HashingSizedStream(_chunks(b'abc'), 4)

    with pytest.raises(errors.RequestError, match='Content-Length'):
        _ = [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_stage_release_upload_writes_real_tempfile_and_calculates_sha256() -> None:
    content = b'abcdef'
    path, sha256 = await _stage_release_upload(_chunks(b'abc', b'def'), len(content))
    try:
        assert path.read_bytes() == content
        assert sha256 == hashlib.sha256(content).hexdigest()
    finally:
        path.unlink(missing_ok=True)


def test_ci_upload_never_buffers_the_whole_request_body() -> None:
    source = inspect.getsource(ci_upload)

    assert 'request.stream()' in source
    assert 'request.body()' not in source


def test_release_upload_stages_then_uses_public_package_multipart_path() -> None:
    stage_source = inspect.getsource(_stage_release_upload)
    service_source = inspect.getsource(ReleaseService.ci_upload_asset_stream)

    assert 'tempfile.mkstemp' in stage_source
    assert 'upload_public_package_to_storage' in service_source
    assert 'upload_stream_to_storage' not in service_source
    assert 'staged_path.unlink' in service_source


def test_ci_multipart_contract_rejects_invalid_hash_and_accepts_contiguous_parts() -> None:
    with pytest.raises(ValueError, match='sha256'):
        CiMultipartInitRequest(version='0.3.2', file_name='setup.exe', file_size=10, sha256='bad')

    request = CiMultipartCompleteRequest(
        version='0.3.2',
        file_name='setup.exe',
        release_id=4,
        file_size=10,
        sha256='a' * 64,
        upload_id='upload-id',
        object_key='desktop/stable/0.3.2/setup.exe',
        parts=[{'part_number': 1, 'etag': 'etag-1'}],
    )
    assert request.parts[0].part_number == 1


def test_ci_multipart_complete_is_idempotent_after_provider_session_disappears() -> None:
    complete_source = inspect.getsource(ReleaseService.ci_multipart_complete)
    stat_index = complete_source.index('_public_release_object_size')
    complete_index = complete_source.index('complete_multipart_on_storage')
    assert stat_index < complete_index
    assert 'completed_size != obj.file_size' in complete_source
    assert 'stat_on_storage' not in complete_source


def test_ci_multipart_complete_verifies_stored_bytes_sha256() -> None:
    """落桶校验必须走 CDN 回读，**不得**走 S3 端点。

    2026-08-23 实测：七牛写入 AK 对这个公共桶既无 HeadObject 权限（403 PermissionDenied），
    也被禁止从 S3 端点 GetObject（403 GetObjectBlocked），因此
    `StorageService.sha256_on_storage`（S3 GET）在生产上**永远不可能成功**。

    ⚠️ 本断言此前写的是 `'StorageService.sha256_on_storage' in complete_source` —— 那是一条
    纯字符串存在性检查：字符串在就绿，完全不能证明那条调用跑得通。该校验 2026-08-11 引入后
    从未在生产成功执行过（0.3.4 发布早于它落地），v0.3.5 首次真正跑到这一步就连续 29 次 500，
    而这条测试全程是绿的。所以现在正反两侧都钉：必须用 CDN 摘要，且不许退回 S3 读。
    """
    complete_source = inspect.getsource(ReleaseService.ci_multipart_complete)
    # 只看代码行：注释里会写「为什么不用 sha256_on_storage」，不剥掉注释这条反向断言会被自己绊倒。
    code_only = '\n'.join(
        line for line in complete_source.splitlines() if not line.lstrip().startswith('#')
    )

    assert '_public_release_object_digest' in code_only
    assert 'sha256_on_storage' not in code_only, 'S3 端点读在这个桶上恒 403，不许退回去'
    assert 'stored_size != obj.file_size' in complete_source
    assert 'stored_sha256 != obj.sha256' in complete_source
    assert 'sha256=stored_sha256' in complete_source


@pytest.mark.asyncio
async def test_public_release_object_digest_streams_and_hashes_real_bytes(monkeypatch) -> None:
    """行为断言（不是源码 grep）：摘要函数确实按流读完整对象并算出正确 SHA-256。"""
    payload = bytes(range(256)) * 8192  # 2 MiB，跨多个 1 MiB 分块
    expected = hashlib.sha256(payload).hexdigest()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['url'] = str(request.url)
        seen['accept_encoding'] = request.headers.get('accept-encoding')
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(
        release_service_module.httpx,
        'AsyncClient',
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )

    result = await release_service_module._public_release_object_digest('https://cdn.example/a.dmg')

    assert result == (expected, len(payload))
    assert seen['url'] == 'https://cdn.example/a.dmg'
    # identity 编码是硬要求：CDN 若 gzip 回传，算出来的是压缩流的哈希，与本地文件永不相等。
    assert seen['accept_encoding'] == 'identity'


@pytest.mark.asyncio
async def test_public_release_object_digest_returns_none_on_http_error(monkeypatch) -> None:
    """读不通时返回 None（由调用方决定是重传还是报错），不得抛穿或谎报成功。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b'<Error><Code>GetObjectBlocked</Code></Error>')

    monkeypatch.setattr(
        release_service_module.httpx,
        'AsyncClient',
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )

    assert await release_service_module._public_release_object_digest('https://cdn.example/a.dmg') is None


@pytest.mark.parametrize(
    ('raw_value', 'expected'),
    [(b'331761433', 331761433), (b'', None), (b'nope', None), (b'0', None)],
)
def test_required_content_length_rejects_unknown_or_empty_bodies(raw_value: bytes, expected: int | None) -> None:
    request = Request({'type': 'http', 'headers': [(b'content-length', raw_value)]})

    if expected is None:
        with pytest.raises(errors.RequestError, match='Content-Length'):
            _required_content_length(request)
    else:
        assert _required_content_length(request) == expected
