import asyncio
import hashlib

from collections.abc import AsyncIterable, AsyncIterator, Sequence
from typing import BinaryIO
from urllib.parse import unquote, urlsplit

import boto3  # type: ignore[import-untyped]
import httpx

from botocore.config import Config  # type: ignore[import-untyped]
from fastapi import UploadFile
from opendal import AsyncOperator
from qiniu import Auth, put_data, put_stream_v2  # type: ignore[import-untyped]

from backend.common.exception import errors
from backend.common.log import log
from backend.plugin.s3.model import S3Storage

IMMUTABLE_SPEECH_PACKAGE_PREFIX = 'speech/sha256/'
# 制品包分片大小；与语音包上传保持一致。
PUBLIC_PACKAGE_PART_BYTES = 4 * 1024 * 1024


def _package_upload_timeout(size: int) -> float:
    """按 100 KiB/s 的保守下限推算大包上传超时，至少 30 分钟。

    `write_stream` 默认按 500 KiB/s 推算且硬顶 1800 秒，对 GB 级制品包必然不够；
    这里给制品包一条独立的、随体积线性增长的预算。
    """
    return max(1800.0, size / (100 * 1024))


def normalize_storage_root(prefix: str | None) -> str:
    """Return the opendal root for a configured object prefix."""
    clean_prefix = (prefix or '').strip('/')
    return f'/{clean_prefix}' if clean_prefix else '/'


def _public_prefix(prefix: str | None) -> str:
    return (prefix or '').strip('/')


def _clean_object_path(path: str) -> str:
    clean_path = path.strip('/')
    if not clean_path:
        raise errors.RequestError(msg='对象路径不能为空')
    parts = clean_path.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise errors.RequestError(msg='对象路径非法')
    return clean_path


def _reject_reserved_immutable_mutation(path: str) -> None:
    """阻止通用接口覆盖或删除内容寻址命名空间中的语音包。"""
    if path.startswith(IMMUTABLE_SPEECH_PACKAGE_PREFIX):
        raise errors.RequestError(msg='不可变语音包命名空间只允许专用 insert-only 上传，禁止通用覆盖或删除')


def _join_url(base_url: str, *parts: str) -> str:
    clean_parts = [part.strip('/') for part in parts if part and part.strip('/')]
    if clean_parts:
        return f'{base_url.rstrip("/")}/{"/".join(clean_parts)}'
    return base_url.rstrip('/')


def get_operator(
    endpoint: str, access_key: str, secret_key: str, bucket: str, prefix: str, region: str
) -> AsyncOperator:
    """
    获取操作

    :param endpoint: 终端节点
    :param access_key: 访问密钥
    :param secret_key: 密钥
    :param bucket: 存储桶
    :param prefix: 前缀
    :param region: 区域
    :return:
    """
    return AsyncOperator(
        's3',
        endpoint=endpoint,
        access_key_id=access_key,
        secret_access_key=secret_key,
        bucket=bucket,
        root=normalize_storage_root(prefix),
        region=region,
    )


def get_operator_for_storage(s3_storage: S3Storage) -> AsyncOperator:
    """Build an opendal operator from a persisted S3 storage config."""
    return get_operator(
        s3_storage.endpoint,
        s3_storage.access_key,
        s3_storage.secret_key,
        s3_storage.bucket,
        s3_storage.prefix or '/',
        s3_storage.region or 'any',
    )


def _provider_object_key(s3_storage: S3Storage, object_key: str) -> str:
    """把相对存储根的对象键转换为供应商桶内完整键。"""
    clean_key = _clean_object_path(object_key)
    prefix = _public_prefix(s3_storage.prefix)
    return '/'.join(part for part in (prefix, clean_key) if part)


def _s3_client(s3_storage: S3Storage):
    """创建短生命周期 S3 客户端，供受控 multipart 原语使用。"""
    return boto3.client(
        's3',
        endpoint_url=s3_storage.endpoint,
        aws_access_key_id=s3_storage.access_key,
        aws_secret_access_key=s3_storage.secret_key,
        region_name=s3_storage.region or 'us-east-1',
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'virtual'},
            retries={'max_attempts': 1, 'mode': 'standard'},
            connect_timeout=20,
            read_timeout=1800,
        ),
    )


async def create_multipart_upload(
    s3_storage: S3Storage,
    object_key: str,
    *,
    content_type: str,
) -> str:
    """在真实 S3 兼容服务创建 multipart 会话。"""
    clean_key = _clean_object_path(object_key)
    _reject_reserved_immutable_mutation(clean_key)
    provider_key = _provider_object_key(s3_storage, clean_key)

    def create() -> str:
        response = _s3_client(s3_storage).create_multipart_upload(
            Bucket=s3_storage.bucket,
            Key=provider_key,
            ContentType=content_type,
        )
        upload_id = response.get('UploadId')
        if not upload_id:
            raise RuntimeError('对象存储未返回 UploadId')
        return str(upload_id)

    try:
        return await asyncio.to_thread(create)
    except Exception as exc:
        log.exception(f'S3 multipart 初始化失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'S3 multipart 初始化失败: {type(exc).__name__}: {exc!s}') from exc


async def upload_multipart_part(
    s3_storage: S3Storage,
    object_key: str,
    *,
    upload_id: str,
    part_number: int,
    file: BinaryIO,
    size: int,
) -> str:
    """上传一个 multipart 分片，并返回完成会话所需的 ETag。"""
    clean_key = _clean_object_path(object_key)
    provider_key = _provider_object_key(s3_storage, clean_key)
    if part_number < 1 or part_number > 10_000:
        raise errors.RequestError(msg='multipart 分片序号必须在 1 到 10000 之间')
    if size <= 0:
        raise errors.RequestError(msg='multipart 分片不能为空')

    def upload() -> str:
        file.seek(0)
        response = _s3_client(s3_storage).upload_part(
            Bucket=s3_storage.bucket,
            Key=provider_key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=file,
            ContentLength=size,
        )
        etag = response.get('ETag')
        if not etag:
            raise RuntimeError('对象存储未返回分片 ETag')
        return str(etag)

    try:
        return await asyncio.to_thread(upload)
    except Exception as exc:
        log.exception(f'S3 multipart 分片上传失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'S3 multipart 分片上传失败: {type(exc).__name__}: {exc!s}') from exc


async def complete_multipart_upload(
    s3_storage: S3Storage,
    object_key: str,
    *,
    upload_id: str,
    parts: Sequence[tuple[int, str]],
) -> None:
    """按序提交 multipart 会话。"""
    clean_key = _clean_object_path(object_key)
    provider_key = _provider_object_key(s3_storage, clean_key)
    ordered = sorted(parts)
    if not ordered or [number for number, _ in ordered] != list(range(1, len(ordered) + 1)):
        raise errors.RequestError(msg='multipart 分片必须从 1 开始连续')

    def complete() -> None:
        _s3_client(s3_storage).complete_multipart_upload(
            Bucket=s3_storage.bucket,
            Key=provider_key,
            UploadId=upload_id,
            MultipartUpload={
                'Parts': [{'PartNumber': number, 'ETag': etag} for number, etag in ordered],
            },
        )

    try:
        await asyncio.to_thread(complete)
    except Exception as exc:
        log.exception(f'S3 multipart 完成失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'S3 multipart 完成失败: {type(exc).__name__}: {exc!s}') from exc


async def abort_multipart_upload(
    s3_storage: S3Storage,
    object_key: str,
    *,
    upload_id: str,
) -> None:
    """幂等终止 multipart 会话并清理供应商侧残留分片。"""
    clean_key = _clean_object_path(object_key)
    provider_key = _provider_object_key(s3_storage, clean_key)

    def abort() -> None:
        _s3_client(s3_storage).abort_multipart_upload(
            Bucket=s3_storage.bucket,
            Key=provider_key,
            UploadId=upload_id,
        )

    try:
        await asyncio.to_thread(abort)
    except Exception as exc:
        response = getattr(exc, 'response', None)
        error = response.get('Error', {}) if isinstance(response, dict) else {}
        if error.get('Code') in {'NoSuchUpload', 'NoSuchKey'}:
            return
        log.exception(f'S3 multipart 终止失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'S3 multipart 终止失败: {type(exc).__name__}: {exc!s}') from exc


def pick_public_storage(storages: Sequence[S3Storage]) -> S3Storage | None:
    """为公开资产（头像 / 帖图 / 封面 / 企业 Logo / 模板图标）挑选写入存储。

    公开资产应落公共桶（``access='public'``）：CDN 直读、前端不再来回签名（07 D1/D3）。
    若环境尚未配置公共桶，则回退到第一个存储行以保持可用（由调用方判空），
    不静默伪造 URL —— 回退仍是真实可写入的桶，只是访问类型欠优。
    """
    if not storages:
        return None
    for s3_storage in storages:
        if getattr(s3_storage, 'access', 'private') == 'public':
            return s3_storage
    return storages[0]


def build_object_url(s3_storage: S3Storage, path: str) -> str:
    """Build the stable storage/CDN URL for an object key below the configured root."""
    clean_path = _clean_object_path(path)
    prefix = _public_prefix(s3_storage.prefix)
    if s3_storage.cdn_domain:
        return _join_url(s3_storage.cdn_domain, prefix, clean_path)
    return _join_url(s3_storage.endpoint, s3_storage.bucket, prefix, clean_path)


def _relative_path_after_base(url: str, base_url: str) -> str | None:
    parsed = urlsplit(url)
    base = urlsplit(base_url.rstrip('/'))

    if parsed.scheme.lower() != base.scheme.lower() or parsed.netloc.lower() != base.netloc.lower():
        return None

    base_path = unquote(base.path).strip('/')
    url_path = unquote(parsed.path).strip('/')
    if not base_path:
        return url_path
    if url_path == base_path:
        return ''
    prefix = f'{base_path}/'
    if url_path.startswith(prefix):
        return url_path[len(prefix) :]
    return None


def _strip_prefix(path: str, prefix: str | None) -> str:
    clean_path = path.strip('/')
    clean_prefix = _public_prefix(prefix)
    if not clean_prefix:
        return _clean_object_path(clean_path)
    if clean_path == clean_prefix:
        raise errors.RequestError(msg='对象路径不能为空')
    expected = f'{clean_prefix}/'
    if not clean_path.startswith(expected):
        raise errors.RequestError(msg='URL 不属于当前 S3 存储前缀')
    return _clean_object_path(clean_path[len(expected) :])


def object_key_from_url(s3_storage: S3Storage, url: str) -> str:
    """
    Resolve a stable CDN/S3 URL back to the object key relative to opendal root.

    The configured storage prefix is part of the public URL but not part of
    keys passed to opendal, because the operator is already rooted there.
    """
    if s3_storage.cdn_domain:
        relative = _relative_path_after_base(url, s3_storage.cdn_domain)
        if relative is not None:
            return _strip_prefix(relative, s3_storage.prefix)

    relative = _relative_path_after_base(url, s3_storage.endpoint)
    if relative is None:
        raise errors.RequestError(msg='URL 不属于已配置的 S3 存储')

    bucket = s3_storage.bucket.strip('/')
    if relative == bucket:
        raise errors.RequestError(msg='对象路径不能为空')
    expected = f'{bucket}/'
    if not relative.startswith(expected):
        raise errors.RequestError(msg='URL 不属于已配置的 S3 存储桶')

    return _strip_prefix(relative[len(expected) :], s3_storage.prefix)


async def presign_read_key(s3_storage: S3Storage, object_key: str, expires_in: int = 3600) -> str:
    """Return a fresh signed read URL for an object key relative to the opendal root.

    Portability note (07 D8): callers hold the stable object key (not a provider
    URL), so signing never needs to reverse-parse a CDN/S3 URL. Switching provider
    only swaps the s3_storage row; the key stays identical.
    """
    op = get_operator_for_storage(s3_storage)
    try:
        signed = await op.presign_read(object_key, expires_in)
    except Exception as e:
        raise errors.ServerError(msg=f'生成 S3 签名 URL 失败: {e!s}')
    return signed.url


async def presign_read_url(s3_storage: S3Storage, url: str, expires_in: int = 3600) -> dict:
    """Return a fresh signed read URL for a stable private storage URL."""
    object_key = object_key_from_url(s3_storage, url)
    signed_url = await presign_read_key(s3_storage, object_key, expires_in)
    return {
        'url': signed_url,
        'expires_in': expires_in,
        'source_url': url,
    }


async def read_bytes(s3_storage: S3Storage, object_key: str) -> bytes:
    """服务端读取私有桶对象字节（presign GET + httpx 取，provider 无关，对称 write_bytes）。

    模块 18 网页发布托管：访客不接触私有桶长效地址，由服务端 read_stream 代吐。
    """
    signed = await presign_read_key(s3_storage, object_key, 300)
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.get(signed)
            response.raise_for_status()
            return response.content
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300] if e.response.text else e.response.reason_phrase
        raise errors.ServerError(msg=f'读取 S3 对象失败: HTTP {e.response.status_code} {detail}')
    except Exception as e:
        log.exception(f'S3 读取失败: {type(e).__name__}: {e!r}')
        raise errors.ServerError(msg=f'读取 S3 对象失败: {type(e).__name__}: {e!s}')


async def stat_object(s3_storage: S3Storage, object_key: str) -> tuple[int, str | None]:
    """直接读取对象元数据，用于发布前证明对象真实存在且大小一致。"""
    clean_path = _clean_object_path(object_key)
    operator = get_operator_for_storage(s3_storage)
    try:
        metadata = await operator.stat(clean_path)
    except Exception as exc:
        raise errors.ServerError(msg=f'S3 对象元数据读取失败: {type(exc).__name__}: {exc!s}') from exc
    if not metadata.is_file:
        raise errors.ServerError(msg=f'S3 对象不是普通文件: {clean_path}')
    return int(metadata.content_length), metadata.etag


async def sha256_object(s3_storage: S3Storage, object_key: str) -> tuple[str, int]:
    """流式读取真实对象并计算 SHA-256，避免发布大模型时把对象整体载入内存。"""
    clean_path = _clean_object_path(object_key)
    signed = await presign_read_key(s3_storage, clean_path, 1800)
    digest = hashlib.sha256()
    size = 0
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=20.0), trust_env=False) as client,
            client.stream('GET', signed) as response,
        ):
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                digest.update(chunk)
                size += len(chunk)
    except httpx.HTTPStatusError as exc:
        body = await exc.response.aread()
        detail = body[:300].decode('utf-8', errors='replace') if body else exc.response.reason_phrase
        raise errors.ServerError(msg=f'校验 S3 对象失败: HTTP {exc.response.status_code} {detail}') from exc
    except Exception as exc:
        log.exception(f'S3 对象校验失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'校验 S3 对象失败: {type(exc).__name__}: {exc!s}') from exc
    return digest.hexdigest(), size


async def read_object_stream(
    s3_storage: S3Storage,
    object_key: str,
    *,
    expected_size: int,
) -> AsyncIterator[bytes]:
    """以有界内存读取对象，供跨存储复制使用。"""
    clean_path = _clean_object_path(object_key)
    signed = await presign_read_key(s3_storage, clean_path, 1800)
    received = 0
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=20.0), trust_env=False) as client,
            client.stream('GET', signed) as response,
        ):
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > expected_size:
                    raise errors.ServerError(msg='跨存储复制源对象大小超过权威记录')
                yield chunk
    except errors.BaseExceptionError:
        raise
    except httpx.HTTPStatusError as exc:
        body = await exc.response.aread()
        detail = body[:300].decode('utf-8', errors='replace') if body else exc.response.reason_phrase
        raise errors.ServerError(msg=f'跨存储读取源对象失败: HTTP {exc.response.status_code} {detail}') from exc
    except Exception as exc:
        log.exception(f'跨存储读取源对象失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'跨存储读取源对象失败: {type(exc).__name__}: {exc!s}') from exc
    if received != expected_size:
        raise errors.ServerError(msg='跨存储复制源对象大小与权威记录不一致')


async def copy_object_between_storages(
    source: S3Storage,
    source_key: str,
    target: S3Storage,
    target_key: str,
    *,
    size: int,
    content_type: str,
) -> None:
    """同存储优先服务端复制，跨存储时以有界流中转。"""
    clean_source = _clean_object_path(source_key)
    clean_target = _clean_object_path(target_key)
    _reject_reserved_immutable_mutation(clean_target)
    same_root = (
        source.endpoint == target.endpoint
        and source.bucket == target.bucket
        and normalize_storage_root(source.prefix) == normalize_storage_root(target.prefix)
    )
    if same_root:
        operator = get_operator_for_storage(target)
        try:
            await operator.copy(clean_source, clean_target)
            return
        except Exception as exc:
            raise errors.ServerError(msg=f'S3 服务端复制失败: {type(exc).__name__}: {exc!s}') from exc
    await write_stream(
        target,
        clean_target,
        read_object_stream(source, clean_source, expected_size=size),
        size=size,
        content_type=content_type,
    )


async def delete_object(s3_storage: S3Storage, object_key: str) -> None:
    """删除指定对象；供受控清理和真实集成测试回收专用对象。"""
    clean_path = _clean_object_path(object_key)
    _reject_reserved_immutable_mutation(clean_path)
    operator = get_operator_for_storage(s3_storage)
    try:
        await operator.delete(clean_path)
    except Exception as exc:
        raise errors.ServerError(msg=f'删除 S3 对象失败: {type(exc).__name__}: {exc!s}') from exc


async def write_stream(
    s3_storage: S3Storage,
    path: str,
    contents: AsyncIterable[bytes] | bytes,
    *,
    size: int,
    content_type: str | None = None,
    timeout_ceiling: float = 1800.0,
) -> None:
    """以有界内存流式上传已知长度对象。

    ``timeout_ceiling`` 是超时与预签名 TTL 的共同上限，默认 30 分钟。GB 级制品包必须由
    调用方显式放宽，否则单次 PUT 会在上限处整体失败且无法续传。
    """
    clean_path = _clean_object_path(path)
    _reject_reserved_immutable_mutation(clean_path)
    operator = get_operator_for_storage(s3_storage)
    try:
        upload_timeout = min(timeout_ceiling, max(30.0, size / (500 * 1024)))
        presign_ttl = int(min(timeout_ceiling, max(300.0, upload_timeout)))
        signed = await operator.presign_write(clean_path, presign_ttl)
        headers = dict(getattr(signed, 'headers', {}) or {})
        if not isinstance(contents, bytes):
            headers.setdefault('Content-Length', str(size))
        if content_type:
            headers.setdefault('Content-Type', content_type)
        timeout = 30 if upload_timeout == 30 else httpx.Timeout(upload_timeout, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.request(
                getattr(signed, 'method', 'PUT') or 'PUT',
                signed.url,
                content=contents,
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        response = exc.response
        detail = response.text[:300] if response.text else response.reason_phrase
        raise errors.ServerError(msg=f'上传文件到 S3 失败: HTTP {response.status_code} {detail}') from exc
    except Exception as exc:
        log.exception(f'S3 流式上传失败: {type(exc).__name__}: {exc!r}')
        detail = str(exc) or repr(exc)
        raise errors.ServerError(msg=f'上传文件到 S3 失败: {type(exc).__name__}: {detail}') from exc


async def write_immutable_speech_package(
    s3_storage: S3Storage,
    path: str,
    file: BinaryIO,
    *,
    size: int,
    content_type: str,
) -> None:
    """用七牛原生 ``insertOnly`` 策略写入语音内容寻址包，禁止覆盖同 key 异内容。

    当前生产公共桶是七牛 Kodo；其实测 S3 兼容 PUT 会忽略 ``If-None-Match``，
    因此这里显式使用 Kodo 上传凭证的 ``insertOnly=1``。未知 provider 不降级为可覆盖 PUT。
    """
    clean_path = _clean_object_path(path)
    if not clean_path.startswith(IMMUTABLE_SPEECH_PACKAGE_PREFIX):
        raise errors.RequestError(msg='不可变语音包必须写入 speech/sha256 内容寻址命名空间')
    hostname = (urlsplit(s3_storage.endpoint).hostname or '').lower()
    if not hostname.endswith('.qiniucs.com'):
        raise errors.ServerError(msg='当前对象存储不支持已验证的语音包 insert-only 上传，拒绝可覆盖写入')

    prefix = _public_prefix(s3_storage.prefix)
    provider_key = '/'.join(part for part in (prefix, clean_path) if part)
    policy = {
        'insertOnly': 1,
        'fsizeMin': size,
        'fsizeLimit': size,
        'mimeLimit': content_type,
        'returnBody': '{"key":$(key),"hash":$(etag),"size":$(fsize)}',
    }
    token = Auth(s3_storage.access_key, s3_storage.secret_key).upload_token(
        s3_storage.bucket,
        provider_key,
        3600,
        policy,
    )

    def upload() -> tuple[dict | None, object]:
        file.seek(0)
        if size <= 4 * 1024 * 1024:
            return put_data(
                token,
                provider_key,
                file.read(),
                mime_type=content_type,
                fname=provider_key.rsplit('/', 1)[-1],
            )
        return put_stream_v2(
            token,
            provider_key,
            file,
            provider_key.rsplit('/', 1)[-1],
            size,
            mime_type=content_type,
            version='v2',
            bucket_name=s3_storage.bucket,
            part_size=4 * 1024 * 1024,
        )

    try:
        result, info = await asyncio.to_thread(upload)
    except Exception as exc:
        log.exception(f'七牛不可变语音包上传失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'七牛不可变语音包上传失败: {type(exc).__name__}: {exc!s}') from exc
    status_code = int(getattr(info, 'status_code', 0) or 0)
    if status_code != 200 or result is None:
        detail = str(getattr(info, 'text_body', '') or getattr(info, 'error', '') or 'unknown error')[:300]
        raise errors.ServerError(msg=f'七牛不可变语音包上传失败: HTTP {status_code} {detail}')
    if result.get('key') != provider_key or int(result.get('size', -1)) != size:
        raise errors.ServerError(msg='七牛不可变语音包上传结果与请求 key/大小不一致')


async def _iterate_binary(file: BinaryIO, chunk_bytes: int) -> AsyncIterator[bytes]:
    """把同步文件对象转成有界内存的异步字节流；读盘放线程池避免阻塞事件循环。"""
    await asyncio.to_thread(file.seek, 0)
    while chunk := await asyncio.to_thread(file.read, chunk_bytes):
        yield chunk


async def write_public_package_stream(
    s3_storage: S3Storage,
    path: str,
    file: BinaryIO,
    *,
    size: int,
    content_type: str,
) -> None:
    """分片上传 GB 级制品包（引擎包 / 模型包）。

    不能走 :func:`write_stream`：那条路径是单次预签名 PUT，``upload_timeout`` 与
    ``presign_ttl`` 都硬顶 1800 秒且不可续传——2 GiB 级的包只要实际吞吐低于约 1.2 MB/s，
    就会在第 1800 秒预签名过期时整体作废，且没有断点续传，只能从零重传。

    **刻意不加 ``insertOnly``**：制品包的 key 内嵌内容摘要，「跳过重复上传」由调用方在上传前
    以服务端复算摘要判定（见 `_reuse_uploaded_model_package`）。若桶内同 key 的对象与本次内容
    不符（上一次上传中断留下的残包），调用方的语义是覆盖重传；用 insert-only 会让这种残包
    永远修不好——每次重试都拿到 ``614 file exists``，却仍指向那份坏字节。
    """
    clean_path = _clean_object_path(path)
    _reject_reserved_immutable_mutation(clean_path)
    hostname = (urlsplit(s3_storage.endpoint).hostname or '').lower()
    if not hostname.endswith('.qiniucs.com'):
        # 非七牛 provider 没有已验证的分片实现，退回预签名 PUT；但必须解除 1800 秒硬顶，
        # 否则大包在这条分支上依然必然超时。
        await write_stream(
            s3_storage,
            path,
            _iterate_binary(file, PUBLIC_PACKAGE_PART_BYTES),
            size=size,
            content_type=content_type,
            timeout_ceiling=_package_upload_timeout(size),
        )
        return

    prefix = _public_prefix(s3_storage.prefix)
    provider_key = '/'.join(part for part in (prefix, clean_path) if part)
    # 只锁大小与 MIME，不锁 insert-only：同 key 异内容的残包必须能被覆盖修复。
    policy = {
        'fsizeMin': size,
        'fsizeLimit': size,
        'mimeLimit': content_type,
        'returnBody': '{"key":$(key),"hash":$(etag),"size":$(fsize)}',
    }
    token = Auth(s3_storage.access_key, s3_storage.secret_key).upload_token(
        s3_storage.bucket,
        provider_key,
        int(_package_upload_timeout(size)),
        policy,
    )

    def upload() -> tuple[dict | None, object]:
        file.seek(0)
        if size <= PUBLIC_PACKAGE_PART_BYTES:
            return put_data(
                token,
                provider_key,
                file.read(),
                mime_type=content_type,
                fname=provider_key.rsplit('/', 1)[-1],
            )
        return put_stream_v2(
            token,
            provider_key,
            file,
            provider_key.rsplit('/', 1)[-1],
            size,
            mime_type=content_type,
            version='v2',
            bucket_name=s3_storage.bucket,
            part_size=PUBLIC_PACKAGE_PART_BYTES,
        )

    try:
        result, info = await asyncio.to_thread(upload)
    except Exception as exc:
        log.exception(f'制品包分片上传失败: {type(exc).__name__}: {exc!r}')
        raise errors.ServerError(msg=f'制品包分片上传失败: {type(exc).__name__}: {exc!s}') from exc
    status_code = int(getattr(info, 'status_code', 0) or 0)
    if status_code != 200 or result is None:
        detail = str(getattr(info, 'text_body', '') or getattr(info, 'error', '') or 'unknown error')[:300]
        raise errors.ServerError(msg=f'制品包分片上传失败: HTTP {status_code} {detail}')
    if result.get('key') != provider_key or int(result.get('size', -1)) != size:
        raise errors.ServerError(msg='制品包上传结果与请求 key/大小不一致')


async def write_bytes(s3_storage: S3Storage, path: str, contents: bytes, content_type: str | None = None) -> None:
    """Write bytes via a short-lived signed PUT URL.

    Qiniu's S3 compatible endpoint can reject opendal's direct write request
    shape inside the ASGI server with HTTP 405. A presigned PUT keeps signing
    server-side while using the storage provider's simple upload path.
    """

    await write_stream(
        s3_storage,
        path,
        contents,
        size=len(contents),
        content_type=content_type,
    )


async def write_file(s3_storage: S3Storage, file: UploadFile) -> None:
    """
    写入文件

    :param s3_storage: S3 存储
    :param file: 上传文件
    :return:
    """
    filename = file.filename
    if not filename:
        raise errors.RequestError(msg='上传文件名不能为空')
    contents = await file.read()
    await write_bytes(s3_storage, filename, contents, file.content_type)
