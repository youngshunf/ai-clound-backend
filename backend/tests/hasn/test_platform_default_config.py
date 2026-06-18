"""平台默认配置（PDC）真实 PG 集成测试（M1/M2 验收）。

零 mock：用真实本地 PostgreSQL(15432) 跑 service get/update/coalesce 全链路；
每个用例在未提交事务内完成（async_db_session 退出即回滚），不污染单行权威表。

覆盖：
- revision 确定性 + 出厂默认兜底（无行）
- update 改 revision + 持久（同事务内可见）
- build_effective_runtime_config coalesce：agent 非空必胜 / null→平台默认 / knobs 透传
- raw=None 且平台四槽全空 → None（保持"全默认"，零行为变化）
- raw=None 且平台有模型 → 注入平台默认

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

import pytest

from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfig
from backend.app.hasn.service.platform_default_config_service import (
    DEFAULT_PLATFORM_CONFIG,
)
from backend.app.hasn.service.platform_default_config_service import (
    platform_default_config_service as svc,
)
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _config(
    *,
    image: list[str] | None = None,
    video: list[str] | None = None,
    main: str | None = None,
    fast: str | None = None,
    vision: str | None = None,
    delegation: str | None = None,
) -> PlatformDefaultConfig:
    return PlatformDefaultConfig.model_validate({
        'node': {
            'media': {
                'image_models': image or ['gpt-image-2'],
                'tts_models': ['tts-1'],
                'stt_models': ['whisper-1'],
                'video_models': video or [],
            }
        },
        'agent_runtime': {'models': {'main': main, 'fast': fast, 'vision': vision, 'delegation': delegation}},
    })


async def test_factory_default_when_no_row() -> None:
    async with async_db_session() as db:
        cfg, rev = await svc.get_effective_config(db)
        # 无行 → 出厂默认（与 config/default.toml [media] 对齐），revision 稳定可比较。
        assert cfg.node.media.image_models == DEFAULT_PLATFORM_CONFIG['node']['media']['image_models']
        # 视频默认空：视频渠道需运营在 new-api 开通后再经 Admin 下发（PV4）。
        assert cfg.node.media.video_models == DEFAULT_PLATFORM_CONFIG['node']['media']['video_models'] == []
        _cfg2, rev2 = await svc.get_effective_config(db)
        assert rev == rev2 and rev != ''


async def test_update_persists_video_models_for_node_downlink() -> None:
    async with async_db_session() as db:
        # 运营下发视频模型 → node.media.video_models 落库并回读（daemon 据此覆盖本机 config）。
        resp = await svc.update_config(db, config=_config(video=['sora-1', 'kling-1']), updated_by='pytest')
        cfg, rev = await svc.get_effective_config(db)
        assert rev == resp.revision
        assert cfg.node.media.video_models == ['sora-1', 'kling-1']


async def test_update_changes_revision_and_persists_in_txn() -> None:
    async with async_db_session() as db:
        _, base_rev = await svc.get_effective_config(db)
        resp = await svc.update_config(db, config=_config(image=['gpt-image-1'], main='gpt-5'), updated_by='pytest')
        assert resp.revision != base_rev
        # 同事务内重读：flush 后可见，配置与 revision 一致（退出回滚不落库）。
        cfg, rev = await svc.get_effective_config(db)
        assert rev == resp.revision
        assert cfg.node.media.image_models == ['gpt-image-1']
        assert cfg.agent_runtime.models.main == 'gpt-5'


async def test_build_effective_runtime_config_coalesce() -> None:
    async with async_db_session() as db:
        await svc.update_config(
            db, config=_config(main='p-main', fast='p-fast', vision='p-vision'), updated_by='pytest'
        )
        # agent: main 显式 / fast 显式 null / 其余缺省 null；knob max_turns=80。
        raw = {'models': {'main': 'a-main', 'fast': None}, 'max_turns': 80}
        eff = await svc.build_effective_runtime_config(db, raw)
        assert eff is not None
        assert eff['models']['main'] == 'a-main'  # agent 非空必胜
        assert eff['models']['fast'] == 'p-fast'  # null → 平台默认
        assert eff['models']['vision'] == 'p-vision'  # 缺省 → 平台默认
        assert eff['models']['delegation'] is None  # 双方皆空 → None
        assert eff['max_turns'] == 80  # knob 原样透传（本期不做平台默认）


async def test_build_effective_none_when_all_empty() -> None:
    async with async_db_session() as db:
        # 平台四槽全空（默认）+ agent 无配置 → None（保持"全默认"语义，零行为变化）。
        await svc.update_config(db, config=_config(), updated_by='pytest')
        assert await svc.build_effective_runtime_config(db, None) is None


async def test_build_effective_platform_only_when_raw_none() -> None:
    async with async_db_session() as db:
        await svc.update_config(db, config=_config(main='only-platform'), updated_by='pytest')
        eff = await svc.build_effective_runtime_config(db, None)
        assert eff is not None and eff['models']['main'] == 'only-platform'
