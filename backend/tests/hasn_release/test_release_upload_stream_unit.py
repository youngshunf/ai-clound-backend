"""桌面端发布产物流式上传的回归契约。"""

import hashlib
import inspect

from collections.abc import AsyncIterator

import pytest

from starlette.requests import Request

from backend.app.hasn_release.api.v1.ci.release import _required_content_length, ci_upload
from backend.app.hasn_release.schema.release import CiMultipartCompleteRequest, CiMultipartInitRequest
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
    stat_index = complete_source.index('stat_on_storage')
    complete_index = complete_source.index('complete_multipart_on_storage')
    assert stat_index < complete_index
    assert 'stat is None or stat.size != obj.file_size' in complete_source


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
