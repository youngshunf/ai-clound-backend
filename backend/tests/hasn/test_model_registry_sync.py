"""模型注册表同步与 Admin 面测试（真实 PostgreSQL + 进程内 HTTP E2E）。

覆盖：
- upsert 三种语义：新增 / 既有只覆盖 new-api 列（人工标注不动）/ 本轮消失标 missing 但不删行
- 幂等：连跑两次，第二次 0 新增、人工标注列一字不改
- 拉不到网关时**整轮放弃**，绝不把现有行统统标 missing（那等于一次抖动清空全平台下发）
- capability 建议值只是建议，同步器一律写 `unclassified` 且 `agent_visible=false`
- Admin 三端点 HTTP E2E（列表过滤 / PATCH 标注 / POST 同步），统一信封

上游数据用**显式夹具**注入（`get_pricing_catalog` 是唯一出网点）：要断言「只覆盖这几列」
就必须精确控制两轮之间的差异。另有一条打真实网关的用例（网关不可达即跳过），证明
客户端确实解得开 `/api/pricing` 的真实信封——这是夹具证不了的那一半。

事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §4
"""

from __future__ import annotations

import uuid

from decimal import Decimal
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

from backend.app.hasn.api.v1.admin.hasn_model_registry import router as admin_model_registry_router
from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry
from backend.app.hasn.service.model_registry_sync_service import (
    model_registry_sync_service,
    suggest_capability,
)
from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.rbac import rbac_verify
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(admin_model_registry_router, prefix='/api/v1/hasn/model-registry')
_REGISTRY = '/api/v1/hasn/model-registry'


def _model(prefix: str) -> str:
    """本轮测试专属的模型名前缀，避免与真实同步进来的 64 行相互干扰。"""
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


def _pricing_row(name: str, *, ratio: float | None = 1.0, vendor_id: int | None = 5, **extra) -> dict:
    """构造一行与生产 `/api/pricing` 同形状的定价数据（2026-08-02 实测字段）。"""
    row = {
        'model_name': name,
        'quota_type': 0,
        'model_ratio': ratio,
        'model_price': 0,
        'owner_by': '',
        'completion_ratio': 1,
        'enable_groups': ['default', 'vip'],
        'supported_endpoint_types': ['openai'],
    }
    if vendor_id is not None:
        row['vendor_id'] = vendor_id
    row.update(extra)
    return row


_VENDORS = {5: '阿里巴巴', 10: 'DeepSeek'}


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    created: list[str] = []
    # 同步是**全表**语义：本轮上游没出现的行一律标 missing。夹具只喂两三个测试模型，
    # 会把本机已同步的真实模型统统标成 missing，污染同一个开发库上的其它测试（PDC 写入校验
    # 就会因此把真实模型判成「网关上已消失」）。故先快照、收尾还原。
    baseline_status: dict[str, str] = {
        name: status
        for name, status in (
            await session.execute(
                sa.select(HasnModelRegistry.model_name, HasnModelRegistry.upstream_status)
            )
        ).all()
    }

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=900_000_101)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    async def _rbac_pass(request: Request) -> None:
        return None

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject
    _APP.dependency_overrides[rbac_verify] = _rbac_pass

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')

    def stub_upstream(rows: list[dict], vendors: dict[int, str] | None = None) -> None:
        """把本轮上游返回钉死成显式夹具（唯一出网点）。"""

        async def _fake(self=None):
            return list(rows), dict(vendors or _VENDORS)

        monkeypatch.setattr(newapi_admin_client, 'get_pricing_catalog', _fake)

    def fail_upstream(error: Exception) -> None:
        async def _boom(self=None):
            raise error

        monkeypatch.setattr(newapi_admin_client, 'get_pricing_catalog', _boom)

    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            stub_upstream=stub_upstream,
            fail_upstream=fail_upstream,
            track=created.extend,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        # 测试造的行是真实落库的，收尾按模型名精确清掉（不碰真实同步进来的行）。
        await session.rollback()
        if created:
            await session.execute(sa.delete(HasnModelRegistry).where(HasnModelRegistry.model_name.in_(created)))
        # 还原被本轮全表同步误标的既有行状态（见上方快照说明）。
        for name, status in baseline_status.items():
            await session.execute(
                sa.update(HasnModelRegistry)
                .where(
                    HasnModelRegistry.model_name == name,
                    HasnModelRegistry.upstream_status != status,
                )
                .values(upstream_status=status)
            )
        await session.commit()
        await session.close()
        await engine.dispose()


async def _row(session, name: str) -> HasnModelRegistry:
    got = (
        await session.execute(sa.select(HasnModelRegistry).where(HasnModelRegistry.model_name == name))
    ).scalar_one_or_none()
    assert got is not None, f'模型 {name} 应已入库'
    return got


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


# ============================ upsert 三种语义 ============================


async def test_新模型插入时待标注且对分身不可见(env) -> None:
    name = _model('sync-new')
    env.track([name])
    env.stub_upstream([_pricing_row(name, ratio=2.5, vendor_id=5)])

    report = await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    assert report.created >= 1
    row = await _row(env.session, name)
    # 能力类别猜不出来就不猜：未标注即不下发，绝不让分身把文生图发给 TTS 模型。
    assert row.capability == 'unclassified'
    assert row.agent_visible is False
    assert row.upstream_status == 'active'
    assert row.vendor_name == '阿里巴巴'
    assert row.relative_cost == Decimal('2.5000')
    assert row.enable_groups == ['default', 'vip']
    # 没被提升成列的计费字段原样留档，供运维核对。
    assert row.cost_extra['completion_ratio'] == 1
    assert row.cost_extra['supported_endpoint_types'] == ['openai']
    assert row.last_synced_time is not None


async def test_既有模型只覆盖newapi列而人工标注一律不动(env) -> None:
    name = _model('sync-keep')
    env.track([name])
    env.stub_upstream([_pricing_row(name, ratio=1.0, vendor_id=5)])
    await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    # 运营标注一遍（这些列是人工权威）。
    row = await _row(env.session, name)
    row.capability = 'video'
    row.inputs = {'image': 'required'}
    row.dialect = 'ali'
    row.quality = 'high'
    row.scenario = '出终稿用'
    row.agent_visible = True
    row.sort_order = 7
    row.cost_tier_override = 'premium'
    await env.session.commit()

    # 网关侧改价 + 换供应商 + 改分组，再同步一轮。
    env.stub_upstream([_pricing_row(name, ratio=3.5, vendor_id=10, enable_groups=['svip'])])
    report = await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    assert report.created == 0, '既有模型不该被当成新增'
    assert report.updated >= 1, 'new-api 列确有变化，应计入 updated'
    row = await _row(env.session, name)
    # new-api 权威列被覆盖
    assert row.relative_cost == Decimal('3.5000')
    assert row.vendor_name == 'DeepSeek'
    assert row.enable_groups == ['svip']
    # 人工标注列一字不动
    assert row.capability == 'video'
    assert row.inputs == {'image': 'required'}
    assert row.dialect == 'ali'
    assert row.quality == 'high'
    assert row.scenario == '出终稿用'
    assert row.agent_visible is True
    assert row.sort_order == 7
    assert row.cost_tier_override == 'premium'


async def test_本轮消失的模型标missing但保留行与标注(env) -> None:
    gone, stays = _model('sync-gone'), _model('sync-stays')
    env.track([gone, stays])
    env.stub_upstream([_pricing_row(gone), _pricing_row(stays)])
    await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    row = await _row(env.session, gone)
    row.capability = 'video'
    row.scenario = '标注不能因为渠道下线就丢'
    await env.session.commit()

    # 这一轮网关上只剩 stays。
    env.stub_upstream([_pricing_row(stays)])
    report = await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    assert report.missing >= 1
    row = await _row(env.session, gone)  # 行还在——删了人工标注就得重标
    assert row.upstream_status == 'missing'
    assert row.capability == 'video'
    assert row.scenario == '标注不能因为渠道下线就丢'
    assert (await _row(env.session, stays)).upstream_status == 'active'

    # 渠道回来 → 自动恢复 active（人工标注仍在）。
    env.stub_upstream([_pricing_row(gone), _pricing_row(stays)])
    await model_registry_sync_service.sync(env.session)
    await env.session.commit()
    row = await _row(env.session, gone)
    assert row.upstream_status == 'active'
    assert row.capability == 'video'


async def test_同步幂等_连跑两次第二次零新增且标注不变(env) -> None:
    name = _model('sync-idem')
    env.track([name])
    # 0.8572 是生产实测倍率：float 不精确，不量化就会每轮误判成「变了」。
    rows = [_pricing_row(name, ratio=0.8572)]
    env.stub_upstream(rows)
    await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    row = await _row(env.session, name)
    row.capability = 'chat'
    row.scenario = '幂等校验'
    await env.session.commit()

    second = await model_registry_sync_service.sync(env.session)
    await env.session.commit()
    assert second.created == 0, '第二次不该有新增'
    assert second.updated == 0, '内容没变时不该报 updated（否则运营每天看到一堆假变更）'

    row = await _row(env.session, name)
    assert row.capability == 'chat'
    assert row.scenario == '幂等校验'
    assert row.relative_cost == Decimal('0.8572')


async def test_网关拉不到时整轮放弃而不是把现有行统统标missing(env) -> None:
    name = _model('sync-outage')
    env.track([name])
    env.stub_upstream([_pricing_row(name)])
    await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    env.fail_upstream(NewApiError('new-api 不可达: connect timeout', endpoint='/pricing'))
    with pytest.raises(NewApiError):
        await model_registry_sync_service.sync(env.session)
    await env.session.rollback()

    # 一次网络抖动不得把下发清空。
    assert (await _row(env.session, name)).upstream_status == 'active'


# ============================ capability 建议值 ============================


def test_能力建议值只作辅助且认得出生产上的真实模型名() -> None:
    # 生产 64 个模型里的真实名字（2026-08-02 拉取）。
    assert suggest_capability('happyhorse-1.1-i2v') == 'video'
    assert suggest_capability('agnes-video-v2.0') == 'video'
    assert suggest_capability('wan2.6-i2v-flash') == 'video'
    assert suggest_capability('agnes-image-2.1-flash') == 'image_generate'
    assert suggest_capability('qwen-image-2.0') == 'image_generate'
    assert suggest_capability('gpt-image-2') == 'image_generate'
    assert suggest_capability('qwen3-tts-flash') == 'tts'
    assert suggest_capability('cosyvoice-v3.5-flash') == 'tts'
    assert suggest_capability('qwen3-asr-flash-filetrans') == 'stt'
    assert suggest_capability('fun-asr-flash-2026-06-15') == 'stt'
    assert suggest_capability('gte-rerank-v2') == 'rerank'
    assert suggest_capability('text-embedding-v4') == 'embedding'
    # embedding 优先于 vision：这个名字两者都含，判成 vision 会让它进错候选池。
    assert suggest_capability('tongyi-embedding-vision-plus-2026-03-06') == 'embedding'
    assert suggest_capability('deepseek-v4-pro') == 'chat'
    # 结构化信号（端点类型）优先于名字关键词。
    assert suggest_capability('mystery-model', ['audio_speech']) == 'tts'


# ============================ Admin 三端点 HTTP E2E ============================


async def test_admin端点_同步与标注与列表过滤(env) -> None:
    video, chat = _model('admin-video'), _model('admin-chat')
    env.track([video, chat])
    env.stub_upstream([_pricing_row(video, ratio=2.5), _pricing_row(chat, ratio=1.0)])

    report = _data(await env.client.post(f'{_REGISTRY}/sync'))
    assert report['created'] >= 2
    assert report['upstream_total'] == 2

    # 列表：按待标注过滤，能看到刚同步进来的两个，且带只读建议值。
    listed = _data(await env.client.get(f'{_REGISTRY}', params={'capability': 'unclassified', 'keyword': 'admin-'}))
    names = {item['model_name'] for item in listed['items']}
    assert {video, chat} <= names
    by_name = {item['model_name']: item for item in listed['items']}
    # 建议值只是建议：名字里有 video 关键词就建议 video，没有就落到 chat；
    # 但入库的 capability 仍是 unclassified、对分身不可见——必须运营确认后才生效。
    assert by_name[video]['suggested_capability'] == 'video'
    assert by_name[chat]['suggested_capability'] == 'chat'
    assert by_name[video]['capability'] == 'unclassified'
    assert by_name[video]['agent_visible'] is False

    pk = by_name[video]['id']
    patched = _data(
        await env.client.patch(
            f'{_REGISTRY}/{pk}',
            json={
                'capability': 'video',
                'inputs': {'image': 'required', 'audio': 'optional'},
                'dialect': 'ali',
                'quality': 'high',
                'scenario': '图生视频终稿',
                'agent_visible': True,
                'sort_order': 3,
            },
        )
    )
    assert patched['capability'] == 'video'
    assert patched['inputs'] == {'image': 'required', 'audio': 'optional'}
    assert patched['agent_visible'] is True
    # 回读一致（不是只改了内存）。
    await env.session.commit()
    row = await _row(env.session, video)
    assert row.capability == 'video'
    assert row.sort_order == 3

    # 非法枚举当场拒，不静默存坏值。
    bad = await env.client.patch(f'{_REGISTRY}/{pk}', json={'capability': 'not-a-capability'})
    assert bad.status_code != 200 or bad.json().get('code') != 200
    bad_inputs = await env.client.patch(f'{_REGISTRY}/{pk}', json={'inputs': {'image': 'maybe'}})
    assert bad_inputs.status_code != 200 or bad_inputs.json().get('code') != 200

    # 传空串清空可选列（JSON 里没法用「不传」表达「清空」）。
    cleared = _data(await env.client.patch(f'{_REGISTRY}/{pk}', json={'quality': '', 'scenario': ''}))
    assert cleared['quality'] is None
    assert cleared['scenario'] is None


# ============================ 真实网关形状 ============================


async def test_真实网关pricing信封能解出模型与供应商表() -> None:
    """打真实 new-api：夹具证不了「我们解得开它真实回的信封」，而那正是上次栽的地方
    （`/api/models/` 注册表生产上是空的，只有 `/api/pricing` 有数据）。网关不可达即跳过。"""
    try:
        rows, vendors = await newapi_admin_client.get_pricing_catalog()
    except NewApiError as exc:
        pytest.skip(f'new-api 不可达，跳过真实网关用例: {exc}')
    assert rows, '生产网关定价表不该是空的（空说明又走回 /api/models/ 那条死路）'
    assert all(row.get('model_name') for row in rows)
    # 供应商名只在信封的兄弟字段里，`get_pricing` 只解 data 是拿不到的。
    assert vendors, 'vendors 表应能解出（否则供应商列会整列为空）'
    assert all(isinstance(k, int) and isinstance(v, str) for k, v in vendors.items())


# ============================ 价格档位（按能力内比价算出） ============================


async def test_价格档位按能力分组算出且不足两个可比模型时不分档(env) -> None:
    from backend.app.hasn.service.model_registry_catalog_service import cost_tier_map

    cheap, mid, dear, lonely = (
        _model('tier-cheap'),
        _model('tier-mid'),
        _model('tier-dear'),
        _model('tier-lonely'),
    )
    env.track([cheap, mid, dear, lonely])
    # 生产实测的三个视频模型倍率：0.5 / 1.5 / 2.5，相对最便宜的是 1x / 3x / 5x。
    env.stub_upstream([
        _pricing_row(cheap, ratio=0.5),
        _pricing_row(mid, ratio=1.5),
        _pricing_row(dear, ratio=2.5),
        _pricing_row(lonely, ratio=9.9),
    ])
    await model_registry_sync_service.sync(env.session)
    await env.session.commit()

    rows = {}
    for name, capability in ((cheap, 'video'), (mid, 'video'), (dear, 'video'), (lonely, 'rerank')):
        row = await _row(env.session, name)
        row.capability = capability
        rows[name] = row
    await env.session.commit()

    tiers = cost_tier_map(rows.values())
    assert tiers[rows[cheap].id] == 'economy'
    assert tiers[rows[mid].id] == 'standard'
    assert tiers[rows[dear].id] == 'premium'
    # 同类里只有它一个 → 整组不分档：唯一选择既不贵也不便宜，标 economy 等于凭空暗示便宜。
    assert rows[lonely].id not in tiers

    # 拉不到价格的行也不分档（绝不编一个默认值让分身照着花主人的钱）。
    rows[cheap].relative_cost = None
    await env.session.commit()
    assert rows[cheap].id not in cost_tier_map(rows.values())
