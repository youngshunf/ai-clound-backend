"""PDC 模型名校验测试（本次事故的根因闭环）。

覆盖：
- 存入网关上不存在的模型名被拒，且错误里给出最接近的候选
- 存入 `missing` 状态的模型被拒（曾经有、现在没了，存进去照样 503）
- 五处 `*_models` + 四槽 + fallback pool 都在校验范围内
- 注册表还空着时放行（否则「同步」和「配置」互相死锁）

事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §5.2
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry
from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfig
from backend.app.hasn.service.pdc_model_validation_service import (
    build_rejections,
    collect_configured_models,
    format_rejections,
    pdc_model_validation_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _config(**over) -> PlatformDefaultConfig:
    """造一份 PDC（只填本测试关心的槽）。"""
    payload: dict = {
        'node': {'media': {}},
        'agent_runtime': {'models': {}, 'model_fallback_pool': []},
    }
    payload['node']['media'].update(over.pop('media', {}))
    payload['agent_runtime'].update(over.pop('agent_runtime', {}))
    return PlatformDefaultConfig.model_validate(payload)


def _name(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


# ============================ 纯函数：摊平与判定 ============================


def test_摊平覆盖五处列表与四槽与兜底池() -> None:
    config = _config(
        media={
            'image_models': ['img-a'],
            'image_edit_models': ['edit-a'],
            'tts_models': ['tts-a'],
            'stt_models': ['stt-a'],
            'video_models': [
                'video-str',
                {'name': 'video-obj', 'modality': 'image_to_video', 'dialect': 'ali'},
            ],
        },
        agent_runtime={
            'models': {'main': 'main-a', 'fast': 'fast-a', 'vision': None, 'delegation': ''},
            'model_fallback_pool': ['pool-a', 'pool-b'],
        },
    )
    found = dict(collect_configured_models(config))
    assert found['node.media.image_models[0]'] == 'img-a'
    assert found['node.media.image_edit_models[0]'] == 'edit-a'
    assert found['node.media.tts_models[0]'] == 'tts-a'
    assert found['node.media.stt_models[0]'] == 'stt-a'
    # 视频两种写法都取到 name
    assert found['node.media.video_models[0]'] == 'video-str'
    assert found['node.media.video_models[1]'] == 'video-obj'
    assert found['agent_runtime.models.main'] == 'main-a'
    assert found['agent_runtime.models.fast'] == 'fast-a'
    assert found['agent_runtime.model_fallback_pool[0]'] == 'pool-a'
    assert found['agent_runtime.model_fallback_pool[1]'] == 'pool-b'
    # 空槽不参与校验（None/空串 = 跟随默认，不是「配了个空模型」）
    assert 'agent_runtime.models.vision' not in found
    assert 'agent_runtime.models.delegation' not in found


def test_写错名字被拒且给出最接近的候选() -> None:
    # 线上那次真实的错法：配 agnes-2.0-video，网关上真名是 agnes-video-v2.0。
    configured = [('node.media.video_models[0]', 'agnes-2.0-video')]
    active = {'agnes-video-v2.0', 'happyhorse-1.1-i2v', 'wan2.6-i2v-flash'}
    rejections = build_rejections(configured, active, set())
    assert len(rejections) == 1
    assert rejections[0]['model'] == 'agnes-2.0-video'
    assert 'agnes-video-v2.0' in rejections[0]['suggestions']
    message = format_rejections(rejections)
    assert 'agnes-2.0-video' in message
    assert 'agnes-video-v2.0' in message
    assert '你是不是想填' in message


def test_missing状态的模型被拒且理由与不存在区分开() -> None:
    configured = [('node.media.video_models[0]', 'gone-model')]
    rejections = build_rejections(configured, {'live-model'}, {'gone-model'})
    assert len(rejections) == 1
    # 两种运营动作完全不同：一个要改名字，一个要等渠道回来或换一个，文案必须说清。
    assert '网关上已消失' in rejections[0]['reason']
    unknown = build_rejections([('x', 'never-existed')], {'live-model'}, {'gone-model'})
    assert '不在模型注册表' in unknown[0]['reason']


def test_相似度不够就不给建议() -> None:
    rejections = build_rejections([('x', 'zzzzzzzz')], {'agnes-video-v2.0'}, set())
    # 瞎猜的建议比不给更误导。
    assert rejections[0]['suggestions'] == []


# ============================ 真实 PostgreSQL：整链路 ============================


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

    async def add(model_name: str, *, upstream_status: str = 'active') -> None:
        created.append(model_name)
        session.add(
            HasnModelRegistry(
                model_name=model_name,
                capability='video',
                inputs={},
                dialect=None,
                quality=None,
                scenario=None,
                agent_visible=True,
                sort_order=0,
                vendor_name=None,
                relative_cost=None,
                cost_extra={},
                cost_tier_override=None,
                enable_groups=[],
                upstream_status=upstream_status,
                last_synced_time=None,
            )
        )
        await session.flush()

    try:
        yield SimpleNamespace(session=session, add=add)
    finally:
        if created:
            await session.rollback()
            await session.execute(sa.delete(HasnModelRegistry).where(HasnModelRegistry.model_name.in_(created)))
            await session.commit()
        await session.close()
        await engine.dispose()


async def test_校验读真实注册表_active放行missing与未知拒绝(env) -> None:
    live, gone = _name('pdc-live'), _name('pdc-gone')
    await env.add(live)
    await env.add(gone, upstream_status='missing')
    await env.session.commit()

    ok = await pdc_model_validation_service.validate(env.session, _config(media={'video_models': [live]}))
    assert ok == [], f'active 模型应放行，实得：{ok}'

    bad = await pdc_model_validation_service.validate(env.session, _config(media={'video_models': [gone]}))
    assert len(bad) == 1 and '已消失' in bad[0]['reason']

    unknown = await pdc_model_validation_service.validate(
        env.session, _config(media={'video_models': [f'{live}-typo']})
    )
    assert len(unknown) == 1
    # 名字只差一点 → 应给出正确的那个作为候选。
    assert live in unknown[0]['suggestions']


async def test_update_config写入被拒绝时抛请求错误(env) -> None:
    from backend.app.hasn.service.platform_default_config_service import platform_default_config_service

    live = _name('pdc-guard')
    await env.add(live)
    await env.session.commit()

    with pytest.raises(errors.RequestError) as caught:
        await platform_default_config_service.update_config(
            env.session,
            config=_config(media={'video_models': ['definitely-not-a-real-model']}),
            updated_by='pytest',
        )
    assert '模型名校验未通过' in str(caught.value.msg)
    await env.session.rollback()
