"""素材站下载 **服务层 + 活体** E2E（A-P2-2·§4.6）。

与 `test_stock_download_ssrf.py`（纯函数、离线）互补：这里跑**真实 PostgreSQL(15432)** 的
provider 目录 → `stock_download_service.download` 全链路，验证「DB 驱动的下载白名单」接线，
并在具备真实出网 + 真实七牛桶时跑通「下载→落私有桶→双登记→artifact.search 搜回」活体闭环。

两段：
1. **DB 驱动下载闸 + 落桶双登记检索（默认跑，真 PG + 真七牛桶，确定性通过、零 mock）**：
   a) 播种 provider 行 → `enabled_download_domains()` 反映其 `download_domains` → 服务据 DB 白名单
      拒非白名单 host / 拒非 https / 无 enabled provider 直接拒（补齐纯函数单测覆盖不到的
      「service 读 DB 白名单」环，均确定性）；
   b) `_store_and_register`（下载**后半程**）用真实 PNG 字节走**真七牛私有桶 upload + 真 PG
      register_asset + hasn_artifacts.record → artifact.search 搜回** → 清理。覆盖 `stock.download`
      除「_stream_download 纯外站取字节」外的**全部**真实基础设施环节——补齐设计 §4.6 核心价值
      「分身造得出却搜不回」的真实 E2E 覆盖（此前该环仅在活体段被 skip，从未真跑）。
   全段不依赖外站出网，在任意机器上确定性通过。
2. **活体全链路（`STOCK_LIVE_E2E=1` 才跑）**：仅剩「_stream_download 真外站取字节」这唯一一步——
   真下载一张公共图 → 落私有桶 → 双登记 → artifact.search 搜回 → 清理。需要**真实出网**（生产/
   非代理网络）+ 真实七牛桶。若本机为 fake-ip 透明代理（外站解析进保留网段 198.18/15），SSRF 闸会
   **正确拒绝**——此时测试显式 skip 并说明「需真实出网环境（生产）」，绝不 mock、绝不降级（零 mock
   零 fake 铁律）。段 1(b) 已用真图字节把这一步之外的落桶/登记/检索全部真实覆盖。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。活体段另需 STOCK_LIVE_E2E=1。
"""

from __future__ import annotations

import os
import socket

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio

from sqlalchemy import delete, select, text, update

from backend.app.hasn_stock.model import HasnStockProviders
from backend.app.hasn_stock.schema.stock_provider import CreateProviderParam
from backend.app.hasn_stock.service.download_service import StockDownloadError, stock_download_service
from backend.app.hasn_stock.service.provider_store import stock_provider_store
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.artifact import ArtifactSearchTool
from backend.app.mcp.tools.stock import StockDownloadTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# 多个真实-DB async 测试共享同一 module 级事件循环（对齐 conftest 的连接池隔离）。
pytestmark = pytest.mark.asyncio(loop_scope='module')

_TEST_PREFIX = 'test_stock_dl_'
# 活体下载目标：Wikimedia 公共示例图（稳定、可匿名下载、image/jpeg）。
_LIVE_HOST = 'upload.wikimedia.org'
_LIVE_URL = 'https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg'
# 真实分身/主人对（本地库既有）。下载路径只用 owner_hasn_id / agent_hasn_id。
_AGENT = 'a_one_66cee7c3'
_OWNER = 'h_wb_0423b018'


def _pname() -> str:
    return f'{_TEST_PREFIX}{uuid4().hex[:10]}'


async def _cleanup_providers() -> None:
    async with async_db_session.begin() as db:
        await db.execute(delete(HasnStockProviders).where(HasnStockProviders.provider.like(f'{_TEST_PREFIX}%')))
    stock_provider_store.invalidate_cache()


@pytest_asyncio.fixture(autouse=True, loop_scope='module')
async def _auto_cleanup() -> AsyncGenerator[None, None]:
    """每个用例前后清掉本族自建 provider 行，避免污染。"""
    await _cleanup_providers()
    yield
    await _cleanup_providers()


async def _seed_provider(host: str) -> int:
    """播种一个 enabled、白名单含 host、支持 image 的 provider，返回 id。"""
    item = await stock_provider_store.create_admin(
        CreateProviderParam(
            provider=_pname(),
            display_name='下载E2E探针',
            media_types=['image'],
            api_key=None,
            download_domains=[host],
            enabled=True,
            priority=999,
            license_terms_url='https://example.com/license',
            remark='下载 E2E 临时行',
        )
    )
    stock_provider_store.invalidate_cache()
    return item.id


def _ctx() -> AgentContext:
    ctx = AgentContext(
        hasn_id=_AGENT,
        owner_id=0,  # owner_id 列实存 hasn_id 字符串；下载路径不读它
        agent_status='active',
        metadata={},
        agent_name='一号',
        owner_hasn_id=_OWNER,
    )
    ctx.work_session_id = None
    return ctx


def _host_resolves_reserved(host: str) -> bool:
    """host 是否被本机解析进保留/私网/环回网段（fake-ip 透明代理特征）。

    是 → 真实出网不可达，活体下载会被 SSRF 闸正确拦截，此环境不具备活体条件。
    """
    import ipaddress

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # 解析不了也视作不具备出网条件
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
            return False  # 有一个真公网 IP → 具备出网条件
    return True


# --------------------------------------------------------------------------- #
# 段 1：DB 驱动下载闸（默认跑，真 PG，确定性通过）
# --------------------------------------------------------------------------- #


async def test_enabled_download_domains_reflects_seeded_provider() -> None:
    """播种 provider 后，服务侧 SSRF 白名单并集应立即包含其 download_domains（DB 权威接线）。"""
    await _seed_provider(_LIVE_HOST)
    domains = await stock_provider_store.enabled_download_domains()
    assert _LIVE_HOST in domains, f'enabled_download_domains 未反映播种域名：{sorted(domains)}'


async def test_download_rejects_non_whitelisted_host_via_db_whitelist() -> None:
    """服务据 DB 白名单拒绝不在白名单内的 host（host 检查在任何网络请求之前，确定性）。"""
    await _seed_provider(_LIVE_HOST)  # 白名单只含 wikimedia
    with pytest.raises(StockDownloadError, match='白名单'):
        await stock_download_service.download(
            owner_hasn_id=_OWNER,
            agent_hasn_id=_AGENT,
            url='https://evil.example.com/x.jpg',  # 不在白名单
            title='should-reject',
        )


async def test_download_rejects_non_https_via_service() -> None:
    """服务拒绝非 https 直链（确定性，先于网络）。"""
    await _seed_provider(_LIVE_HOST)
    with pytest.raises(StockDownloadError, match='https'):
        await stock_download_service.download(
            owner_hasn_id=_OWNER,
            agent_hasn_id=_AGENT,
            url='http://upload.wikimedia.org/x.jpg',  # 白名单内但非 https
            title='should-reject',
        )


async def test_download_rejects_when_no_provider_configured() -> None:
    """无任何 enabled provider（空白名单）→ 服务直接拒绝，不做通用下载器。

    确定性：临时把库内所有 enabled provider 停用（让白名单为空）→ 断言拒绝 → finally 恢复。
    真 PG、零 mock，任意机器上都能确定性通过（不再因库里有 seed provider 而 skip）。
    """
    async with async_db_session.begin() as db:
        enabled_ids = (
            (await db.execute(select(HasnStockProviders.id).where(HasnStockProviders.enabled.is_(True))))
            .scalars()
            .all()
        )
    try:
        if enabled_ids:
            async with async_db_session.begin() as db:
                await db.execute(
                    update(HasnStockProviders).where(HasnStockProviders.id.in_(enabled_ids)).values(enabled=False)
                )
        stock_provider_store.invalidate_cache()
        domains = await stock_provider_store.enabled_download_domains()
        assert not domains, f'临时停用后白名单应为空：{sorted(domains)}'
        with pytest.raises(StockDownloadError, match='未配置'):
            await stock_download_service.download(
                owner_hasn_id=_OWNER, agent_hasn_id=_AGENT, url=_LIVE_URL, title='no-provider'
            )
    finally:
        # 恢复被临时停用的 provider（不留副作用）。
        if enabled_ids:
            async with async_db_session.begin() as db:
                await db.execute(
                    update(HasnStockProviders).where(HasnStockProviders.id.in_(enabled_ids)).values(enabled=True)
                )
        stock_provider_store.invalidate_cache()


def _real_png_bytes() -> bytes:
    """生成一张**真实合法**的小 PNG（8×8 皇家蓝）作为落桶输入。

    这是真图片字节（有正确 PNG 魔数 + IHDR/IDAT/IEND，register_asset 能解析宽高），
    不是 mock——它替代的仅是「从外站取字节」这一步（该步须真实出网），而落桶/登记/检索
    这三段全部走真实基础设施。Pillow 是后端既有依赖（资产维度提取用）。
    """
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new('RGB', (8, 8), (37, 99, 235)).save(buf, format='PNG')
    return buf.getvalue()


async def test_store_and_register_with_real_bytes_roundtrip() -> None:
    """下载**后半程**真实基础设施 E2E（落桶→双登记→检索），默认跑，零 mock。

    真实图片字节 → `_store_and_register`（真七牛私有桶 upload + 真 PG register_asset +
    hasn_artifacts.record）→ `artifact.search` 按标题搜回 → 清理。覆盖 `stock.download`
    除「_stream_download 纯外站取字节」外的**全部**环节（那一步须真实出网，见段 2）。

    补齐设计 §4.6 核心价值「分身造得出却搜不回」缺口的真实覆盖——此前该环仅在活体段被 skip。
    """
    await _seed_provider(_LIVE_HOST)  # 让 provider_for_domain 能解析出 provider 元数据
    data = _real_png_bytes()
    title = f'stock-store-live-{uuid4().hex[:8]}'
    # 语义描述里放一个**只在 description、绝不在 title 出现**的独特词，用来证明 summary 驱动检索召回。
    desc_token = f'sunsetbeach{uuid4().hex[:8]}'
    description = f'a woman walking on the {desc_token} at golden hour'
    # url 仅用于 provider_for_domain(host) + 文件名推导；字节来自上面的真实 PNG。
    fake_source_url = f'https://{_LIVE_HOST}/{title}.png'

    # SLF001：本用例刻意直测下载「后半程」私有接缝 _store_and_register（外站取字节须真实出网，见段 2）。
    result = await stock_download_service._store_and_register(
        owner_hasn_id=_OWNER,
        agent_hasn_id=_AGENT,
        data=data,
        content_type='image/png',
        url=fake_source_url,
        title=title,
        description=description,
    )

    asset_uri = result.get('asset_uri', '')
    artifact_id = result.get('artifact_id')
    assert asset_uri.startswith('hasn://asset/'), f'asset_uri 形状不对：{asset_uri}'
    assert artifact_id, 'artifact_id 缺失'
    assert result.get('kind') == 'image', f'kind 应为 image：{result.get("kind")}'
    assert result.get('size_bytes') == len(data), 'size_bytes 应等于真实字节数'

    # artifact.search 按标题片段搜回 → 断言落桶产物可被检索。
    found = await ArtifactSearchTool().execute(_ctx(), {'query': title})
    ids = [it.get('artifact_id') or it.get('id') for it in found.get('items', [])]
    assert artifact_id in ids, f'落桶产物 {artifact_id} 未被 artifact.search 搜回：{ids}'

    # ⭐ summary 驱动召回：按**只出现在 description（→ summary）里、不在 title 里**的独特词搜，
    # 也能搜回。证明「补 summary 提升检索召回」真生效——素材文件名无意义时靠语义描述也能找回。
    by_desc = await ArtifactSearchTool().execute(_ctx(), {'query': desc_token})
    desc_ids = [it.get('artifact_id') or it.get('id') for it in by_desc.get('items', [])]
    assert artifact_id in desc_ids, (
        f'落桶产物 {artifact_id} 未能按 description 语义词 {desc_token} 搜回（summary 未生效）：{desc_ids}'
    )

    # 清理本次创建的 artifact/asset 行（真实删除，不留残行）。
    aid = asset_uri.rsplit('/', 1)[-1]
    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_artifacts WHERE artifact_id = :i'), {'i': artifact_id})
        await db.execute(text('DELETE FROM hasn_assets WHERE asset_id = :a'), {'a': aid})


# --------------------------------------------------------------------------- #
# 段 2：活体下载闭环（STOCK_LIVE_E2E=1 才跑；需真实出网 + 真实七牛桶）
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not os.environ.get('STOCK_LIVE_E2E'), reason='活体下载需 STOCK_LIVE_E2E=1（真实出网 + 七牛桶）')
async def test_live_download_upload_register_search_roundtrip() -> None:
    """真下载公共图 → 落私有桶 → 双登记 → artifact.search 搜回 → 清理（零 mock）。"""
    if _host_resolves_reserved(_LIVE_HOST):
        pytest.skip(
            f'本机将 {_LIVE_HOST} 解析进保留/私网网段（fake-ip 透明代理）→ 真实出网不可达，'
            f'SSRF 闸会正确拦截。活体下载须在真实出网环境（生产/非代理网络）运行，绝不 mock/降级。'
        )

    await _seed_provider(_LIVE_HOST)
    ctx = _ctx()
    title = f'stock-dl-live-{uuid4().hex[:8]}'
    tool = StockDownloadTool()

    result = await tool.execute(ctx, {'url': _LIVE_URL, 'title': title})

    asset_uri = result.get('asset_uri', '')
    artifact_id = result.get('artifact_id')
    assert asset_uri.startswith('hasn://asset/'), f'asset_uri 形状不对：{asset_uri}'
    assert artifact_id, 'artifact_id 缺失'
    assert result.get('kind') == 'image', f'kind 应为 image：{result.get("kind")}'
    assert (result.get('size_bytes') or 0) > 0, 'size_bytes 应 > 0'

    # artifact.search 按标题片段搜回 → 断言下载产物可被检索（补齐分身「造得出却搜不回」缺口）。
    found = await ArtifactSearchTool().execute(ctx, {'query': title})
    ids = [it.get('artifact_id') or it.get('id') for it in found.get('items', [])]
    assert artifact_id in ids, f'下载产物 {artifact_id} 未被 artifact.search 搜回：{ids}'

    # 清理本次创建的 artifact/asset 行（真实删除，不留残行）。
    aid = asset_uri.rsplit('/', 1)[-1]
    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_artifacts WHERE artifact_id = :i'), {'i': artifact_id})
        await db.execute(text('DELETE FROM hasn_assets WHERE asset_id = :a'), {'a': aid})
