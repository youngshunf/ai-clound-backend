"""素材站目录存取真实-PG 集成测试（A-P2-0）。

零 mock：真实本地 PostgreSQL(15432)，跑 admin CRUD（加密写 / 掩码读）、failover 候选选择、
SSRF 下载白名单并集、source enum 缓存兜底全链路，结束清理自建行（provider LIKE 'test_stock_%'）。

覆盖 §4.5 / §7 关键不变量：
1. api_key **明文只进不出**——写入加密落库，读回只回 configured+masked（尾 4 位），密文 ≠ 明文。
2. 更新 api_key：None 不改、空串清空、非空加密轮换。
3. provider 唯一冲突 → ConflictError。
4. failover 候选 = enabled ∧ 支持 media_type，按 priority 升序。
5. enabled_download_domains = 所有 enabled 行 download_domains 并集（小写）；禁用行不入白名单。
6. provider_for_domain 反查（精确 + 子域后缀）。
7. cached_source_enum 缓存冷/失效 → 内置三站兜底；缓存热 → enabled 按 priority。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio

from sqlalchemy import delete, select

from backend.app.hasn_stock.model import HasnStockProviders
from backend.app.hasn_stock.schema.stock_provider import CreateProviderParam, UpdateProviderParam
from backend.app.hasn_stock.service.provider_store import _SEED_PROVIDERS, stock_provider_store
from backend.common.exception import errors
from backend.common.security.encryption import key_encryption
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# 多个真实-DB async 测试共享同一 module 级事件循环，避免连接池被上一个已关闭 loop 回收。
pytestmark = pytest.mark.asyncio(loop_scope='session')

_TEST_PREFIX = 'test_stock_'


def _pname() -> str:
    """唯一 provider 标识（测试隔离 + 结束清理用前缀匹配）。"""
    return f'{_TEST_PREFIX}{uuid4().hex[:10]}'


async def _cleanup() -> None:
    """删掉本测试族自建的所有行（前缀匹配），并清进程缓存。"""
    async with async_db_session.begin() as db:
        await db.execute(delete(HasnStockProviders).where(HasnStockProviders.provider.like(f'{_TEST_PREFIX}%')))
    stock_provider_store.invalidate_cache()


@pytest_asyncio.fixture(autouse=True, loop_scope='session')
async def _auto_cleanup() -> AsyncGenerator[None, None]:
    """每个用例前后都清场，避免相互污染 + 不给库留测试残行。"""
    await _cleanup()
    yield
    await _cleanup()


# --------------------------------------------------------------------------- #
# 加密写 / 掩码读（§7 铁律：api_key 明文只进不出）
# --------------------------------------------------------------------------- #


async def test_create_encrypts_api_key_and_masks_on_read() -> None:
    provider = _pname()
    plaintext = 'sk_live_secretXYZW'
    item = await stock_provider_store.create_admin(
        CreateProviderParam(
            provider=provider,
            display_name='测试站',
            media_types=['image', 'video'],
            api_key=plaintext,
            download_domains=['cdn.example.com'],
            enabled=True,
            priority=9001,
        )
    )
    # 出参绝不含明文：只回 configured + 尾 4 位掩码。
    assert item.api_key_configured is True
    assert item.api_key_masked == '****XYZW'
    assert plaintext not in (item.api_key_masked or '')
    dumped = item.model_dump_json()
    assert plaintext not in dumped, 'api_key 明文绝不能出现在序列化出参里'

    # 库里落的是密文（≠ 明文），且能解回明文（仅内部）。
    async with async_db_session() as db:
        row = (
            await db.execute(select(HasnStockProviders).where(HasnStockProviders.provider == provider))
        ).scalar_one()
    assert row.api_key_cipher is not None
    assert row.api_key_cipher != plaintext
    assert key_encryption.decrypt(row.api_key_cipher) == plaintext


async def test_internal_view_decrypts_but_item_masks() -> None:
    provider = _pname()
    plaintext = 'pixkey_ABCDefgh'
    await stock_provider_store.create_admin(
        CreateProviderParam(provider=provider, media_types=['image'], api_key=plaintext, priority=9002)
    )
    # 内部运行时视图（execute 用）解密后拿明文——但这只在服务层内部流转，绝不出 API。
    view = await stock_provider_store.resolve_source(source=provider)
    assert view is not None
    assert view.api_key == plaintext
    # admin item 永远掩码（只露尾 4 位）。
    item = next(i for i in await stock_provider_store.list_admin() if i.provider == provider)
    assert item.api_key_masked == '****' + plaintext[-4:]
    assert item.api_key_masked == '****efgh'


async def test_update_api_key_none_keeps_empty_clears_nonempty_rotates() -> None:
    provider = _pname()
    created = await stock_provider_store.create_admin(
        CreateProviderParam(provider=provider, media_types=['image'], api_key='orig_key_1234', priority=9003)
    )
    pid = created.id

    # None 不改。
    item = await stock_provider_store.update_admin(pid, UpdateProviderParam(display_name='改名了'))
    assert item.display_name == '改名了'
    assert item.api_key_configured is True
    assert item.api_key_masked == '****1234'

    # 非空 → 加密轮换。
    item = await stock_provider_store.update_admin(pid, UpdateProviderParam(api_key='rotated_key_9999'))
    assert item.api_key_configured is True
    assert item.api_key_masked == '****9999'

    # 空串 → 清空 key。
    item = await stock_provider_store.update_admin(pid, UpdateProviderParam(api_key=''))
    assert item.api_key_configured is False
    assert item.api_key_masked is None


async def test_create_duplicate_provider_conflicts() -> None:
    provider = _pname()
    await stock_provider_store.create_admin(CreateProviderParam(provider=provider, media_types=['image']))
    with pytest.raises(errors.ConflictError):
        await stock_provider_store.create_admin(CreateProviderParam(provider=provider, media_types=['video']))


async def test_delete_admin_idempotent_return() -> None:
    provider = _pname()
    created = await stock_provider_store.create_admin(CreateProviderParam(provider=provider, media_types=['image']))
    assert await stock_provider_store.delete_admin(created.id) is True
    assert await stock_provider_store.delete_admin(created.id) is False


# --------------------------------------------------------------------------- #
# failover 候选选择：enabled ∧ 支持 media_type，按 priority 升序
# --------------------------------------------------------------------------- #


async def test_failover_chain_selects_enabled_supporting_by_priority() -> None:
    p_hi = _pname()  # 支持 image，enabled，优先级高（数字小）
    p_lo = _pname()  # 支持 image，enabled，优先级低（数字大）
    p_off = _pname()  # 支持 image，但 disabled
    p_video = _pname()  # 只支持 video
    await stock_provider_store.create_admin(
        CreateProviderParam(provider=p_lo, media_types=['image'], enabled=True, priority=9200)
    )
    await stock_provider_store.create_admin(
        CreateProviderParam(provider=p_hi, media_types=['image', 'video'], enabled=True, priority=9100)
    )
    await stock_provider_store.create_admin(
        CreateProviderParam(provider=p_off, media_types=['image'], enabled=False, priority=9150)
    )
    await stock_provider_store.create_admin(
        CreateProviderParam(provider=p_video, media_types=['video'], enabled=True, priority=9120)
    )

    chain = await stock_provider_store.failover_chain(media_type='image')
    mine = [v.provider for v in chain if v.provider.startswith(_TEST_PREFIX)]
    # 只含 enabled ∧ 支持 image；禁用与只支持 video 的被排除；按 priority 升序。
    assert mine == [p_hi, p_lo]
    assert p_off not in mine
    assert p_video not in mine

    # video 链只含支持 video 的 enabled 行。
    vchain_all = await stock_provider_store.failover_chain(media_type='video')
    vchain = [v.provider for v in vchain_all if v.provider.startswith(_TEST_PREFIX)]
    assert vchain == [p_hi, p_video]  # 9100 < 9120


async def test_resolve_source_missing_returns_none() -> None:
    assert await stock_provider_store.resolve_source(source=_pname()) is None


# --------------------------------------------------------------------------- #
# SSRF 下载白名单：enabled 行 download_domains 并集；反查 provider
# --------------------------------------------------------------------------- #


async def test_enabled_download_domains_union_excludes_disabled() -> None:
    p_on = _pname()
    p_off = _pname()
    await stock_provider_store.create_admin(
        CreateProviderParam(
            provider=p_on,
            media_types=['image'],
            download_domains=['Images.MyStock.com', 'cdn.mystock.com'],  # 大小写混入——应被归一小写
            enabled=True,
            priority=9300,
        )
    )
    await stock_provider_store.create_admin(
        CreateProviderParam(
            provider=p_off,
            media_types=['image'],
            download_domains=['secret.disabled.example'],
            enabled=False,
            priority=9301,
        )
    )
    domains = await stock_provider_store.enabled_download_domains()
    assert 'images.mystock.com' in domains  # 归一小写
    assert 'cdn.mystock.com' in domains
    assert 'secret.disabled.example' not in domains, '禁用行的域名绝不能进 SSRF 白名单'


async def test_provider_for_domain_exact_and_subdomain() -> None:
    provider = _pname()
    await stock_provider_store.create_admin(
        CreateProviderParam(
            provider=provider,
            display_name='反查站',
            media_types=['image'],
            download_domains=['assets.revlookup.example'],
            license_terms_url='https://revlookup.example/license',
            enabled=True,
            priority=9400,
        )
    )
    # 精确命中。
    v = await stock_provider_store.provider_for_domain(host='assets.revlookup.example')
    assert v is not None and v.provider == provider
    assert v.license_terms_url == 'https://revlookup.example/license'
    # 子域后缀命中（img.assets.revlookup.example 命中 assets.revlookup.example）。
    v2 = await stock_provider_store.provider_for_domain(host='img.assets.revlookup.example')
    assert v2 is not None and v2.provider == provider
    # 不相关域名不命中。
    assert await stock_provider_store.provider_for_domain(host='evil.example') is None


# --------------------------------------------------------------------------- #
# source enum 缓存兜底（input_schema 同步渲染路径）
# --------------------------------------------------------------------------- #


async def test_cached_source_enum_cold_fallback_then_warm_reflects_enabled() -> None:
    # 冷缓存（刚 invalidate）→ 回内置三站兜底（input_schema 同步渲染路径的 best-effort）。
    stock_provider_store.invalidate_cache()
    assert stock_provider_store.cached_source_enum() == list(_SEED_PROVIDERS)

    # 建站后 create_admin 内部会 invalidate；暖缓存（execute 前 tools/list 会读带 use_cache=True 的 catalog）。
    provider = _pname()
    await stock_provider_store.create_admin(
        CreateProviderParam(provider=provider, media_types=['image'], enabled=True, priority=9500)
    )
    await stock_provider_store.get_catalog(use_cache=True)
    assert provider in stock_provider_store.cached_source_enum()

    # 禁用后失效 + 重新暖 → 不再出现。
    created = next(i for i in await stock_provider_store.list_admin() if i.provider == provider)
    await stock_provider_store.update_admin(created.id, UpdateProviderParam(enabled=False))
    await stock_provider_store.get_catalog(use_cache=True)
    assert provider not in stock_provider_store.cached_source_enum()
