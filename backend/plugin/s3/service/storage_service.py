"""统一对象存储服务（StorageService）。

收口所有 S3 写入与签名读取，是上传/签名的唯一入口（07 设计事实源）：

- **公私分桶（07 D1/D3）**：按业务 `category` 决定 `access`（public/private），
  写入对应 `s3_storage` 行。public 走 CDN 直读不签名；private 才签名。
- **provider 无关签名（07 D8）**：签名器按 `s3_storage.sign_strategy` 分发
  （`s3_presign` / `cdn_timestamp` / `qiniu_private` / `nginx_secure_link`），provider 差异只活在这一处。
- **可移植性（07 D8）**：DB 只持有 `storage_id + object_key`（不存 provider URL），
  展示时由 `cdn_domain`/`endpoint` 现拼；换 provider/CDN 仅改 `s3_storage` 行。

零 fake：缺对应访问类型的存储空间、签名策略不被支持/缺 key 时，直接抛错暴露，不伪造 URL。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import posixpath

from dataclasses import dataclass
from itertools import starmap
from typing import TYPE_CHECKING
from urllib.parse import quote

from backend.common.exception import errors
from backend.database.redis import redis_client
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import (
    build_object_url,
    presign_read_key,
    read_bytes,
    stat_object,
    write_bytes,
)
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.plugin.s3.model import S3Storage

# category → (access, 默认签名 TTL 秒)。public 不签名 TTL 取 None（07 §4）。
CATEGORY_POLICY: dict[str, tuple[str, int | None]] = {
    'user_avatar': ('public', None),
    'post_image': ('public', None),
    'general_file': ('public', None),
    'dm_attachment': ('private', 3600),
    'private_doc': ('private', 3600),
    # 网页发布制品（模块 18）：私有桶；语义独立于 dm_attachment，**不触发 extract 抽取流水线**
    # （extract 由 register_asset(extract_status='pending') 这层显式触发，本类别不进那条路径）。
    'published_artifact': ('private', 3600),
    # downloadable_local 应用引擎分发包（模块 14 / FILMPUB）：**公共桶、不签名、无 TTL**。
    # daemon 是无鉴权纯 GET 下载（install.rs::http_get_bytes）+ sha256 校验，URL 必须长效公开，
    # 不能是会过期的签名 URL——故归 public，与 user_avatar 等同策略。
    'film_engine': ('public', None),
    # 桌面端发布产物（REL/hasn_release）：**公共桶、不签名、无 TTL**。
    # 官网下载页/Tauri updater 直接读 CDN 长效直链，桌面端 ATS 要求 https（见 build_object_url）；
    # CI 出包后把二进制交云端此处入桶，复用云端既有七牛，CI 不再需要任何七牛凭据。
    'release_asset': ('public', None),
    # 通用语音模型分发包（SPCAT-4）：**公共桶、不签名、无 TTL**。
    # daemon 无鉴权纯 GET 下载 + sha256 + 包级 Ed25519 双重校验，URL 内嵌在签名 catalog 里、
    # 必须长效 https（桌面端 ATS），不能是会过期的签名 URL——故归 public，同 release_asset 策略。
    'speech_model': ('public', None),
}

# category → 对象前缀目录（key 的第一段，content-addressed 之上）。
CATEGORY_DIR: dict[str, str] = {
    'user_avatar': 'avatars',
    'post_image': 'posts',
    'general_file': 'files',
    'dm_attachment': 'dm',
    'private_doc': 'docs',
    'published_artifact': 'published',
    'film_engine': 'film-engine',
    'speech_model': 'speech-models',
}

# 缓存 margin：缓存 TTL = 签名有效期 - margin，保证缓存命中时签名仍有效（1c）。
SIGN_CACHE_MARGIN_SECONDS = 120


@dataclass(frozen=True)
class ObjectRef:
    """一次上传的稳定引用。DB 只需持有 storage_id + object_key（07 D8 可移植）。"""

    storage_id: int
    object_key: str
    access: str
    stable_url: str  # public 为 CDN 直读 URL；private 为内部稳定 URL（不可直接公开访问）
    mime: str
    size: int


@dataclass(frozen=True)
class ObjectStat:
    """对象存储返回的真实文件元数据。"""

    size: int
    etag: str | None


def _category_policy(category: str) -> tuple[str, int | None]:
    policy = CATEGORY_POLICY.get(category)
    if policy is None:
        raise errors.RequestError(msg=f'未知上传类别: {category}')
    return policy


def _pick_storage(storages: Sequence[S3Storage], access: str) -> S3Storage:
    for storage in storages:
        if getattr(storage, 'access', 'private') == access:
            return storage
    raise errors.ServerError(msg=f'未配置 access={access} 的 S3 存储空间')


def _ext_of(filename: str | None) -> str:
    if not filename:
        return ''
    ext = posixpath.splitext(filename)[1].lower()
    # 防御非法/超长扩展名
    return ext if 0 < len(ext) <= 12 and ext.startswith('.') else ''


def _build_key(category: str, *, filename: str | None, data: bytes) -> str:
    """content-addressed key（07 D7）：{dir}/{YYYY}/{MM}/{DD}/{md5[:12]}{ext}。"""
    directory = CATEGORY_DIR.get(category, 'files')
    digest = hashlib.md5(data).hexdigest()[:12]
    now = timezone.now()
    return f'{directory}/{now:%Y/%m/%d}/{digest}{_ext_of(filename)}'


def _cdn_sign_key(storage: S3Storage) -> str:
    """CDN 时间戳防盗链密钥来源：remark JSON 的 cdn_sign_key，或环境变量。

    零 fake：cdn_timestamp 策略缺 key 时抛错，不静默降级到不签名。
    """
    remark = getattr(storage, 'remark', None)
    if remark:
        try:
            parsed = json.loads(remark)
            if isinstance(parsed, dict) and parsed.get('cdn_sign_key'):
                return str(parsed['cdn_sign_key'])
        except (ValueError, TypeError):
            pass
    env_key = os.getenv('S3_CDN_SIGN_KEY')
    if env_key:
        return env_key
    raise errors.ServerError(
        msg='sign_strategy=cdn_timestamp 但未配置 CDN 时间戳防盗链密钥(remark.cdn_sign_key / S3_CDN_SIGN_KEY)'
    )


class StorageService:
    """统一存储服务。所有写入与签名读取必经此处。"""

    @staticmethod
    async def _storages(db: AsyncSession) -> Sequence[S3Storage]:
        storages = await s3_storage_dao.get_all(db)
        if not storages:
            raise errors.ServerError(msg='未配置任何 S3 存储')
        return storages

    @staticmethod
    async def get_storage(db: AsyncSession, storage_id: int) -> S3Storage:
        storage = await s3_storage_dao.get(db, storage_id)
        if not storage:
            raise errors.ServerError(msg=f'S3 存储 {storage_id} 不存在')
        return storage

    @classmethod
    async def upload(
        cls,
        db: AsyncSession,
        data: bytes,
        *,
        category: str,
        filename: str | None = None,
        content_type: str | None = None,
        key: str | None = None,
    ) -> ObjectRef:
        """按 category 选公/私桶写入，返回稳定引用。

        :param key: 调用方可显式指定对象 key（保留既有 avatars/images 命名方案）；
                    缺省则按 content-addressed 规则生成。
        """
        access, _ = _category_policy(category)
        storage = _pick_storage(await cls._storages(db), access)
        object_key = key or _build_key(category, filename=filename, data=data)
        await write_bytes(storage, object_key, data, content_type)
        return ObjectRef(
            storage_id=storage.id,
            object_key=object_key,
            access=access,
            stable_url=build_object_url(storage, object_key),
            mime=content_type or 'application/octet-stream',
            size=len(data),
        )

    @staticmethod
    def public_url(storage: S3Storage, object_key: str) -> str:
        """公开资产的 CDN 直读 URL（不签名）。"""
        return build_object_url(storage, object_key)

    @classmethod
    async def _sign_one(cls, storage: S3Storage, object_key: str, expires_in: int) -> str:
        """按 sign_strategy 分发签名（07 D8）。provider 差异只在此处。"""
        strategy = getattr(storage, 'sign_strategy', 's3_presign')
        if strategy == 's3_presign':
            return await presign_read_key(storage, object_key, expires_in)
        if strategy == 'cdn_timestamp':
            return cls._cdn_timestamp_url(storage, object_key, expires_in)
        if strategy == 'qiniu_private':
            return cls._qiniu_private_url(storage, object_key, expires_in)
        if strategy == 'nginx_secure_link':
            return cls._nginx_secure_link_url(storage, object_key, expires_in)
        raise errors.ServerError(msg=f'未支持的 sign_strategy: {strategy}')

    @staticmethod
    def _qiniu_private_url(storage: S3Storage, object_key: str, expires_in: int) -> str:
        """七牛私有空间下载凭证（e+token）：私有 bucket 经 CDN「回源鉴权」交付。

        七牛侧「回源鉴权」与「时间戳防盗链」互斥，私有 bucket 官方建议用回源鉴权，
        此时终端访问凭证是 Kodo 私有下载 token，而非 cdn_timestamp 的 sign/t：
        token = AK:urlsafe_b64(hmac_sha1(SK, '<url>?e=<deadline>'))。
        """
        if not storage.cdn_domain:
            raise errors.ServerError(msg='sign_strategy=qiniu_private 但该存储未配置 cdn_domain')
        if not storage.access_key or not storage.secret_key:
            raise errors.ServerError(msg='sign_strategy=qiniu_private 但缺少 access_key/secret_key')
        prefix = (storage.prefix or '').strip('/')
        path = '/' + '/'.join(p for p in (prefix, object_key.strip('/')) if p)
        base = storage.cdn_domain.rstrip('/') + quote(path, safe='/')
        deadline = int(timezone.now().timestamp()) + expires_in
        to_sign = f'{base}?e={deadline}'
        digest = hmac.new(storage.secret_key.encode(), to_sign.encode(), hashlib.sha1).digest()
        token = f'{storage.access_key}:{base64.urlsafe_b64encode(digest).decode()}'
        return f'{to_sign}&token={token}'

    @staticmethod
    def _cdn_timestamp_url(storage: S3Storage, object_key: str, expires_in: int) -> str:
        """七牛 CDN 时间戳防盗链（07 D6）：sign=md5(key + path + t_hex)。"""
        if not storage.cdn_domain:
            raise errors.ServerError(msg='sign_strategy=cdn_timestamp 但该存储未配置 cdn_domain')
        sign_key = _cdn_sign_key(storage)
        prefix = (storage.prefix or '').strip('/')
        path = '/' + '/'.join(p for p in (prefix, object_key.strip('/')) if p)
        encoded_path = quote(path, safe='/')
        expire_ts = int(timezone.now().timestamp()) + expires_in
        t_hex = format(expire_ts, 'x')
        sign = hashlib.md5(f'{sign_key}{encoded_path}{t_hex}'.encode()).hexdigest()
        base = storage.cdn_domain.rstrip('/')
        return f'{base}{encoded_path}?sign={sign}&t={t_hex}'

    @staticmethod
    def _nginx_secure_link_url(storage: S3Storage, object_key: str, expires_in: int) -> str:
        """Nginx secure_link：md5(secret + path + expire) → base64url。"""
        sign_key = _cdn_sign_key(storage)
        if not storage.cdn_domain:
            raise errors.ServerError(msg='sign_strategy=nginx_secure_link 但该存储未配置 cdn_domain')
        prefix = (storage.prefix or '').strip('/')
        path = '/' + '/'.join(p for p in (prefix, object_key.strip('/')) if p)
        expire_ts = int(timezone.now().timestamp()) + expires_in
        raw = f'{sign_key}{path}{expire_ts}'.encode()
        digest = hashlib.md5(raw).digest()
        md5b64 = base64.urlsafe_b64encode(digest).decode().rstrip('=')
        base = storage.cdn_domain.rstrip('/')
        return f'{base}{quote(path, safe="/")}?md5={md5b64}&expires={expire_ts}'

    @classmethod
    async def signed_url(
        cls,
        db: AsyncSession,
        *,
        storage_id: int,
        object_key: str,
        expires_in: int = 3600,
    ) -> str:
        """私有对象的临时签名读 URL（live 签名，无缓存）。"""
        storage = await cls.get_storage(db, storage_id)
        return await cls._sign_one(storage, object_key, expires_in)

    @classmethod
    async def read_bytes(cls, db: AsyncSession, *, storage_id: int, object_key: str) -> bytes:
        """服务端读取私有桶对象的全部字节（平台级新增，模块 18 首个消费者）。

        访客经 /s/{slug}/content 取制品时，服务端经此代吐内容，访客拿不到私有桶长效地址。
        """
        storage = await cls.get_storage(db, storage_id)
        return await read_bytes(storage, object_key)

    @classmethod
    async def stat(cls, db: AsyncSession, *, storage_id: int, object_key: str) -> ObjectStat:
        """读取真实对象元数据；对象缺失或 provider 失败时显式报错。"""
        storage = await cls.get_storage(db, storage_id)
        size, etag = await stat_object(storage, object_key)
        return ObjectStat(size=size, etag=etag)

    @classmethod
    async def read_stream(
        cls,
        db: AsyncSession,
        *,
        storage_id: int,
        object_key: str,
        chunk_size: int = 65536,
    ) -> AsyncIterator[bytes]:
        """服务端流式读私有桶对象（异步生成器，逐 chunk yield）。

        single-html 制品代吐用；制品 ≤25MB（[01] §7 不变量 5），整读后分块下发。
        """
        data = await cls.read_bytes(db, storage_id=storage_id, object_key=object_key)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    # ---- Redis 缓存层（1c：margin TTL，缓存 TTL < 签名有效期，命中即免二次签名）----

    @staticmethod
    def _cache_key(storage_id: int, object_key: str) -> str:
        return f's3:signed:{storage_id}:{object_key}'

    @classmethod
    async def signed_url_cached(
        cls,
        db: AsyncSession,
        *,
        storage_id: int,
        object_key: str,
        expires_in: int = 3600,
    ) -> str:
        """带缓存的签名读 URL。命中返回缓存；未命中 live 签名并 SETEX(ttl-margin)。"""
        rk = cls._cache_key(storage_id, object_key)
        cached = await redis_client.get(rk)
        if cached:
            return cached
        url = await cls.signed_url(db, storage_id=storage_id, object_key=object_key, expires_in=expires_in)
        await redis_client.setex(rk, max(1, expires_in - SIGN_CACHE_MARGIN_SECONDS), url)
        return url

    @classmethod
    async def signed_urls_cached(
        cls,
        db: AsyncSession,
        *,
        items: Sequence[tuple[int, str]],
        expires_in: int = 3600,
    ) -> dict[tuple[int, str], str]:
        """批量签名（MGET 命中 + 仅对未命中 live 签名）。返回 {(storage_id, object_key): url}。"""
        if not items:
            return {}
        cache_keys = list(starmap(cls._cache_key, items))
        cached_vals = await redis_client.mget(cache_keys)
        result: dict[tuple[int, str], str] = {}
        misses: list[tuple[int, str]] = []
        for item, val in zip(items, cached_vals, strict=True):
            if val:
                result[item] = val
            else:
                misses.append(item)
        if not misses:
            return result
        # 按 storage_id 预取存储行，避免逐个未命中重复读库
        storages: dict[int, S3Storage] = {}
        ttl = max(1, expires_in - SIGN_CACHE_MARGIN_SECONDS)
        for sid, ok in misses:
            storage = storages.get(sid)
            if storage is None:
                storage = await cls.get_storage(db, sid)
                storages[sid] = storage
            url = await cls._sign_one(storage, ok, expires_in)
            await redis_client.setex(cls._cache_key(sid, ok), ttl, url)
            result[sid, ok] = url
        return result


storage_service: StorageService = StorageService()
