"""素材站目录存取（A-P2-0）：加密写、掩码读、进程内目录缓存（TTL 60s）、failover 候选选择。

- **加密写**：`api_key` 明文经 `key_encryption.encrypt` 落 `api_key_cipher`，明文绝不落库/回显（对齐 secret_store）。
- **掩码读**：admin 读回 `api_key_configured`（bool）+ `api_key_masked`（尾 4 位），**不回显明文**。
- **目录缓存**：`tools/list` 渲染 `source` enum 频繁读全表 → 进程内缓存 TTL 60s（后台改配置至多一分钟 schema 跟上）。
  `execute` 时**不走缓存**、以 DB 为权威复校（§4.5）。
- **failover 候选**：`enabled ∧ 支持 media_type`，按 `priority` 升序——供 StockService 默认 failover 链。
"""

from __future__ import annotations

import time

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.app.hasn_stock.model import HasnStockProviders
from backend.app.hasn_stock.schema.stock_provider import StockProviderItem
from backend.common.exception import errors
from backend.common.security.encryption import key_encryption
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.hasn_stock.schema.stock_provider import CreateProviderParam, UpdateProviderParam

# 目录缓存 TTL（秒）：source enum 渲染读全表用，后台改配置至多此时长后 schema 跟上。
_CATALOG_TTL_SECONDS = 60.0
# 内置三站（source enum 冷缓存兜底；DB 权威复校在 execute，enum 只是提示）。
_SEED_PROVIDERS = ('pexels', 'pixabay', 'coverr')


@dataclass(frozen=True)
class ProviderView:
    """provider 的运行时视图（含解密后的 api_key，仅 StockService 内部用，绝不出 API）。"""

    provider: str
    display_name: str
    media_types: tuple[str, ...]
    api_key: str | None
    download_domains: tuple[str, ...]
    enabled: bool
    priority: int
    license_terms_url: str | None


def _mask_key(plaintext: str | None) -> str | None:
    """api_key 掩码：仅露尾 4 位（如 ****wxyz）。不足 4 位全掩。"""
    if not plaintext:
        return None
    tail = plaintext[-4:] if len(plaintext) >= 4 else ''
    return f'****{tail}'


def _decrypt_key(cipher: str | None) -> str | None:
    """解密 api_key 密文（失败返回 None，不抛——密钥轮换/脏数据不该炸整条链）。"""
    if not cipher:
        return None
    try:
        return key_encryption.decrypt(cipher)
    except Exception:
        return None


def _to_view(row: HasnStockProviders) -> ProviderView:
    """DB 行 → 运行时视图（解密 api_key）。"""
    return ProviderView(
        provider=row.provider,
        display_name=row.display_name or row.provider,
        media_types=tuple(row.media_types or []),
        api_key=_decrypt_key(row.api_key_cipher),
        download_domains=tuple(row.download_domains or []),
        enabled=bool(row.enabled),
        priority=int(row.priority),
        license_terms_url=row.license_terms_url,
    )


class StockProviderStore:
    """素材站目录存取 + 进程内缓存。"""

    def __init__(self) -> None:
        # 进程内目录缓存：(过期时刻, 视图列表)。多 worker 各自缓存，TTL 内不回源。
        self._cache: tuple[float, list[ProviderView]] | None = None

    def _now(self) -> float:
        return time.monotonic()

    async def _load_all_views(self) -> list[ProviderView]:
        """DB 权威读全表 → 运行时视图（含解密 key）。"""
        async with async_db_session() as db:
            rows = (
                (await db.execute(select(HasnStockProviders).order_by(HasnStockProviders.priority.asc())))
                .scalars()
                .all()
            )
        return [_to_view(r) for r in rows]

    async def get_catalog(self, *, use_cache: bool = True) -> list[ProviderView]:
        """取全量 provider 视图。use_cache=True 走 TTL 60s 缓存（source enum 渲染）；False 强制回源（execute 复校）。"""
        if use_cache and self._cache is not None:
            expires_at, views = self._cache
            if self._now() < expires_at:
                return views
        views = await self._load_all_views()
        self._cache = (self._now() + _CATALOG_TTL_SECONDS, views)
        return views

    def invalidate_cache(self) -> None:
        """管理面增删改后清缓存（本 worker 立即生效；其它 worker 至多 TTL 后跟上）。"""
        self._cache = None

    def cached_source_enum(self) -> list[str]:
        """同步取 enabled provider 标识（source enum schema 渲染，best-effort）。

        `input_schema` 是同步 property，不能 await——故读进程内缓存快照；缓存冷/过期 → 回内置三站兜底。
        这只是给分身的**提示**，真权威在 execute 的 DB 复校（§4.5）。
        """
        if self._cache is not None:
            expires_at, views = self._cache
            if self._now() < expires_at:
                return [v.provider for v in sorted(views, key=lambda x: x.priority) if v.enabled]
        return list(_SEED_PROVIDERS)

    async def enabled_sources(self, *, media_type: str | None = None) -> list[str]:
        """当前 enabled 的 provider 标识列表（source enum 渲染用），按 priority 升序。

        media_type 给定则再筛「支持该媒体类型」的站。
        """
        views = await self.get_catalog(use_cache=True)
        out = [
            v.provider
            for v in sorted(views, key=lambda x: x.priority)
            if v.enabled and (media_type is None or media_type in v.media_types)
        ]
        return out

    async def failover_chain(self, *, media_type: str) -> list[ProviderView]:
        """默认 failover 链：enabled ∧ 支持 media_type，按 priority 升序。以 DB 为权威（execute 用，不走缓存）。"""
        views = await self.get_catalog(use_cache=False)
        chain = [v for v in views if v.enabled and media_type in v.media_types]
        chain.sort(key=lambda x: x.priority)
        return chain

    async def resolve_source(self, *, source: str) -> ProviderView | None:
        """按 provider 标识取单站视图（execute 复校用，以 DB 为权威，不走缓存）。不存在返回 None。"""
        views = await self.get_catalog(use_cache=False)
        for v in views:
            if v.provider == source:
                return v
        return None

    async def enabled_download_domains(self) -> set[str]:
        """所有 enabled 行的 download_domains 并集（stock.download SSRF 白名单，DB 权威）。"""
        views = await self.get_catalog(use_cache=False)
        domains: set[str] = set()
        for v in views:
            if v.enabled:
                domains.update(d.lower() for d in v.download_domains if isinstance(d, str) and d)
        return domains

    async def provider_for_domain(self, *, host: str) -> ProviderView | None:
        """按下载 host 反查所属 provider（下载登记 meta 带 provider/license 用）。"""
        host = host.lower()
        views = await self.get_catalog(use_cache=False)
        for v in views:
            if v.enabled and any(host == d.lower() or host.endswith('.' + d.lower()) for d in v.download_domains if d):
                return v
        return None

    # ---------------------------------------------------------------- admin CRUD（掩码/加密）

    async def list_admin(self) -> list[StockProviderItem]:
        """admin 列表：api_key 掩码不回显明文。"""
        async with async_db_session() as db:
            rows = (
                (await db.execute(select(HasnStockProviders).order_by(HasnStockProviders.priority.asc())))
                .scalars()
                .all()
            )
        return [self._to_item(r) for r in rows]

    def _to_item(self, row: HasnStockProviders) -> StockProviderItem:
        plaintext = _decrypt_key(row.api_key_cipher)
        return StockProviderItem(
            id=row.id,
            provider=row.provider,
            display_name=row.display_name or '',
            media_types=list(row.media_types or []),
            api_key_configured=bool(row.api_key_cipher),
            api_key_masked=_mask_key(plaintext),
            download_domains=list(row.download_domains or []),
            enabled=bool(row.enabled),
            priority=int(row.priority),
            license_terms_url=row.license_terms_url,
            remark=row.remark,
            created_time=row.created_time,
            updated_time=row.updated_time,
        )

    async def get_admin(self, provider_id: int) -> StockProviderItem | None:
        """admin 单条详情（掩码）。"""
        async with async_db_session() as db:
            row = await db.get(HasnStockProviders, provider_id)
        return self._to_item(row) if row is not None else None

    async def create_admin(self, params: CreateProviderParam) -> StockProviderItem:
        """新增素材站（api_key 明文加密落库）。provider 唯一冲突 → ConflictError。"""
        media_types = _clean_media_types(params.media_types)
        cipher = key_encryption.encrypt(params.api_key) if params.api_key and params.api_key.strip() else None
        async with async_db_session.begin() as db:
            dup = (
                await db.execute(select(HasnStockProviders.id).where(HasnStockProviders.provider == params.provider))
            ).scalar_one_or_none()
            if dup is not None:
                raise errors.ConflictError(msg=f'素材站 {params.provider} 已存在')
            row = HasnStockProviders(
                provider=params.provider,
                display_name=params.display_name or params.provider,
                media_types=media_types,
                api_key_cipher=cipher,
                download_domains=_clean_domains(params.download_domains),
                enabled=params.enabled,
                priority=params.priority,
                license_terms_url=params.license_terms_url,
                remark=params.remark,
            )
            db.add(row)
            await db.flush()
            item = self._to_item(row)
        self.invalidate_cache()
        return item

    async def update_admin(self, provider_id: int, params: UpdateProviderParam) -> StockProviderItem:
        """更新素材站。api_key：None 不改、空串清空、非空加密覆盖。"""
        async with async_db_session.begin() as db:
            row = await db.get(HasnStockProviders, provider_id)
            if row is None:
                raise errors.NotFoundError(msg='素材站不存在')
            if params.display_name is not None:
                row.display_name = params.display_name
            if params.media_types is not None:
                row.media_types = _clean_media_types(params.media_types)
            if params.download_domains is not None:
                row.download_domains = _clean_domains(params.download_domains)
            if params.enabled is not None:
                row.enabled = params.enabled
            if params.priority is not None:
                row.priority = params.priority
            if params.license_terms_url is not None:
                row.license_terms_url = params.license_terms_url or None
            if params.remark is not None:
                row.remark = params.remark or None
            if params.api_key is not None:
                # 空串=清空 key；非空=加密覆盖（轮换）。
                row.api_key_cipher = key_encryption.encrypt(params.api_key) if params.api_key.strip() else None
            await db.flush()
            item = self._to_item(row)
        self.invalidate_cache()
        return item

    async def delete_admin(self, provider_id: int) -> bool:
        """删除素材站。返回是否删除了记录。"""
        async with async_db_session.begin() as db:
            row = await db.get(HasnStockProviders, provider_id)
            if row is None:
                return False
            await db.delete(row)
        self.invalidate_cache()
        return True


def _clean_media_types(values: list[str] | None) -> list[str]:
    """媒体类型去噪：只留 image/video 且去重保序。"""
    out: list[str] = []
    for v in values or []:
        v = (v or '').strip().lower()
        if v in ('image', 'video') and v not in out:
            out.append(v)
    return out


def _clean_domains(values: list[str] | None) -> list[str]:
    """下载域名去噪：去空白、去重保序、统一小写。"""
    out: list[str] = []
    for v in values or []:
        v = (v or '').strip().lower()
        if v and v not in out:
            out.append(v)
    return out


stock_provider_store = StockProviderStore()
