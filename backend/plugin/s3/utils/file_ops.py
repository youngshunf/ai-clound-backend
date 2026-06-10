from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

import httpx

from fastapi import UploadFile
from opendal import AsyncOperator

from backend.common.exception import errors
from backend.common.log import log
from backend.plugin.s3.model import S3Storage


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


def _join_url(base_url: str, *parts: str) -> str:
    clean_parts = [part.strip('/') for part in parts if part and part.strip('/')]
    if clean_parts:
        return f"{base_url.rstrip('/')}/{'/'.join(clean_parts)}"
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
        return url_path[len(prefix):]
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
    return _clean_object_path(clean_path[len(expected):])


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

    return _strip_prefix(relative[len(expected):], s3_storage.prefix)


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


async def write_bytes(s3_storage: S3Storage, path: str, contents: bytes, content_type: str | None = None) -> None:
    """Write bytes via a short-lived signed PUT URL.

    Qiniu's S3 compatible endpoint can reject opendal's direct write request
    shape inside the ASGI server with HTTP 405. A presigned PUT keeps signing
    server-side while using the storage provider's simple upload path.
    """
    clean_path = _clean_object_path(path)
    op = get_operator_for_storage(s3_storage)
    try:
        signed = await op.presign_write(clean_path, 300)
        headers = dict(getattr(signed, 'headers', {}) or {})
        if content_type:
            headers.setdefault('Content-Type', content_type)
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.request(
                getattr(signed, 'method', 'PUT') or 'PUT',
                signed.url,
                content=contents,
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        response = e.response
        detail = response.text[:300] if response.text else response.reason_phrase
        raise errors.ServerError(msg=f'上传文件到 S3 失败: HTTP {response.status_code} {detail}')
    except Exception as e:
        log.exception(f'S3 上传失败: {type(e).__name__}: {e!r}')
        detail = str(e) or repr(e)
        raise errors.ServerError(msg=f'上传文件到 S3 失败: {type(e).__name__}: {detail}')


async def write_file(s3_storage: S3Storage, file: UploadFile) -> None:
    """
    写入文件

    :param s3_storage: S3 存储
    :param file: 上传文件
    :return:
    """
    contents = await file.read()
    await write_bytes(s3_storage, file.filename, contents, file.content_type)
