"""模型注册表下发端点测试（真实 PostgreSQL + 进程内 HTTP E2E）。

覆盖：
- 三条硬闸：未标注 / 网关上已消失 / 未对分身放开，一律不下发
- 出参**不含**原始计费倍率与内部计费字段（下发即泄漏计费口径）
- 价格档位在「本次实际下发的这批」里按能力分组比价得出
- `registry_revision`：内容变则变、内容不变则稳定（daemon 据此判断是否重拉）

事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §5
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app.platform_config import router as app_platform_router
from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry
from backend.app.hasn.service.model_registry_downlink_service import (
    compute_registry_revision,
    model_registry_downlink_service,
)
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_platform_router, prefix='/api/v1/hasn/app/platform')
_PLATFORM = '/api/v1/hasn/app/platform'


def _name(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    created: list[str] = []

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=900_000_202)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')

    async def add(model_name: str, **columns) -> HasnModelRegistry:
        """真实插一行注册表（收尾按名精确清掉，不碰真实同步进来的行）。"""
        created.append(model_name)
        row = HasnModelRegistry(
            model_name=model_name,
            capability=columns.pop('capability', 'video'),
            inputs=columns.pop('inputs', {}),
            dialect=columns.pop('dialect', None),
            quality=columns.pop('quality', None),
            scenario=columns.pop('scenario', None),
            agent_visible=columns.pop('agent_visible', True),
            sort_order=columns.pop('sort_order', 0),
            vendor_name=columns.pop('vendor_name', None),
            relative_cost=columns.pop('relative_cost', None),
            cost_extra=columns.pop('cost_extra', {}),
            cost_tier_override=columns.pop('cost_tier_override', None),
            enable_groups=columns.pop('enable_groups', []),
            upstream_status=columns.pop('upstream_status', 'active'),
            last_synced_time=columns.pop('last_synced_time', None),
        )
        session.add(row)
        await session.flush()
        return row

    try:
        yield SimpleNamespace(client=client, session=session, add=add)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        if created:
            await session.rollback()
            await session.execute(sa.delete(HasnModelRegistry).where(HasnModelRegistry.model_name.in_(created)))
            await session.commit()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


async def test_三条硬闸_未标注与已消失与未放开一律不下发(env) -> None:
    good = _name('dl-good')
    await env.add(good, capability='video', agent_visible=True, inputs={'image': 'required'})
    unclassified = _name('dl-unclassified')
    await env.add(unclassified, capability='unclassified', agent_visible=True)
    missing = _name('dl-missing')
    await env.add(missing, capability='video', agent_visible=True, upstream_status='missing')
    hidden = _name('dl-hidden')
    await env.add(hidden, capability='video', agent_visible=False)
    await env.session.commit()

    payload = await model_registry_downlink_service.list_downlink(env.session)
    names = {entry['name'] for group in payload['models'].values() for entry in group}
    assert good in names
    # 未标注：能力猜不出来，猜错会让分身把文生图发给 TTS 模型。
    assert unclassified not in names
    # 网关上已消失：发出去只会 503。
    assert missing not in names
    # 未对分身放开：运营还没确认。
    assert hidden not in names


async def test_下发出参不含原始计费倍率与内部计费字段(env) -> None:
    name = _name('dl-cost')
    await env.add(
        name,
        capability='video',
        relative_cost=2.5,
        cost_extra={'completion_ratio': 1, 'quota_type': 0},
        enable_groups=['default', 'vip'],
        vendor_name='阿里巴巴',
        quality='high',
        scenario='出终稿',
        dialect='ali',
        inputs={'image': 'required', 'audio': 'optional'},
    )
    await env.session.commit()

    payload = await model_registry_downlink_service.list_downlink(env.session)
    entry = next(e for e in payload['models']['video'] if e['name'] == name)
    # 该带的语义都在
    assert entry['capability'] == 'video'
    assert entry['inputs'] == {'image': 'required', 'audio': 'optional'}
    assert entry['quality'] == 'high'
    assert entry['scenario'] == '出终稿'
    assert entry['dialect'] == 'ali'
    assert entry['vendor'] == '阿里巴巴'
    # 内部计费口径一律不下发
    assert 'relative_cost' not in entry
    assert 'cost_extra' not in entry
    assert 'enable_groups' not in entry


async def test_价格档位在下发这批里按能力分组比价(env) -> None:
    cheap, mid, dear = _name('dl-cheap'), _name('dl-mid'), _name('dl-dear')
    lonely = _name('dl-lonely')
    # 生产实测视频三档：0.5 / 1.5 / 2.5，相对最便宜的 1x / 3x / 5x。
    await env.add(cheap, capability='video', relative_cost=0.5)
    await env.add(mid, capability='video', relative_cost=1.5)
    await env.add(dear, capability='video', relative_cost=2.5)
    # 同类只有它一个 → 不分档（唯一选择既不贵也不便宜）。
    await env.add(lonely, capability='rerank', relative_cost=9.9)
    await env.session.commit()

    payload = await model_registry_downlink_service.list_downlink(env.session)
    video = {e['name']: e for e in payload['models']['video']}
    assert video[cheap]['cost_tier'] == 'economy'
    assert video[mid]['cost_tier'] == 'standard'
    assert video[dear]['cost_tier'] == 'premium'
    rerank = {e['name']: e for e in payload['models']['rerank']}
    assert 'cost_tier' not in rerank[lonely]

    # 人工覆盖优先于算出来的。
    row = (
        await env.session.execute(sa.select(HasnModelRegistry).where(HasnModelRegistry.model_name == cheap))
    ).scalar_one()
    row.cost_tier_override = 'premium'
    await env.session.commit()
    payload = await model_registry_downlink_service.list_downlink(env.session)
    video = {e['name']: e for e in payload['models']['video']}
    assert video[cheap]['cost_tier'] == 'premium'


async def test_registry_revision_内容变则变内容不变则稳定(env) -> None:
    name = _name('dl-rev')
    row = await env.add(name, capability='video', agent_visible=True)
    await env.session.commit()

    first = (await model_registry_downlink_service.list_downlink(env.session))['registry_revision']
    again = (await model_registry_downlink_service.list_downlink(env.session))['registry_revision']
    assert first == again, '内容没变时 revision 必须稳定，否则 daemon 每次都白重拉'

    row.quality = 'high'
    await env.session.commit()
    assert (await model_registry_downlink_service.list_downlink(env.session))[
        'registry_revision'
    ] != first, '内容变了 revision 必须变，否则 daemon 缓存永远刷不新'

    # 行数变化也必须改指纹（只看 max(updated_time) 会漏掉删行/加行）。
    rows = list((await env.session.execute(sa.select(HasnModelRegistry))).scalars())
    assert compute_registry_revision(rows) != compute_registry_revision(rows[:-1])


async def test_下发端点HTTP_E2E_按能力分组且带revision(env) -> None:
    name = _name('dl-http')
    await env.add(name, capability='video', agent_visible=True, inputs={'image': 'required'}, relative_cost=1.5)
    await env.session.commit()

    payload = _data(await env.client.get(f'{_PLATFORM}/models'))
    assert 'registry_revision' in payload
    assert payload['total'] >= 1
    assert name in {e['name'] for e in payload['models']['video']}

    # 旧端点保留一版兼容（标 deprecated，P4 移除）——还没升级的 daemon 不能整个失去视频清单。
    legacy = _data(await env.client.get(f'{_PLATFORM}/video-models'))
    assert 'models' in legacy
