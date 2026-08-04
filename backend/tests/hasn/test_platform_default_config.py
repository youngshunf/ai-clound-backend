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

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry
from backend.app.hasn.model.hasn_platform_default_config import HasnPlatformDefaultConfig
from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfig, VideoModelSpec
from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded
from backend.app.hasn.service.pdc_model_validation_service import collect_configured_models
from backend.app.hasn.service.platform_default_config_service import (
    DEFAULT_PLATFORM_CONFIG,
    _record_media_upgrade_advisories,
    _warn_media_upgrade_advisories_once,
    collect_media_upgrade_advisories,
    normalize_legacy_media_gateway_defaults,
)
from backend.app.hasn.service.platform_default_config_service import (
    platform_default_config_service as svc,
)
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def _seed_registry(db: AsyncSession, config: PlatformDefaultConfig) -> None:
    """把这份配置里用到的模型名全部登记进注册表（active）。

    P3 起 `update_config` 会校验模型名必须在注册表且 active——这正是本次事故的根因闭环
    （存进网关上不存在的名字，打到网关只会 503）。本函数让测试显式声明「这些模型在网关上真有」，
    而不是绕过校验；随外层事务一起回滚，不污染注册表。
    """
    existing = set(
        (await db.execute(sa.select(HasnModelRegistry.model_name))).scalars()
    )
    for _path, name in collect_configured_models(config):
        if name in existing:
            continue
        existing.add(name)
        db.add(
            HasnModelRegistry(
                model_name=name,
                capability='unclassified',
                inputs={},
                dialect=None,
                quality=None,
                scenario=None,
                agent_visible=False,
                sort_order=0,
                vendor_name=None,
                relative_cost=None,
                cost_extra={},
                cost_tier_override=None,
                enable_groups=[],
                upstream_status='active',
                last_synced_time=None,
            )
        )
    await db.flush()


def _config(
    *,
    image: list[str] | None = None,
    image_edit: list[str] | None = None,
    tts: list[str] | None = None,
    stt: list[str] | None = None,
    video: list[str] | None = None,
    main: str | None = None,
    fast: str | None = None,
    vision: str | None = None,
    delegation: str | None = None,
    fallback_pool: list[str] | None = None,
) -> PlatformDefaultConfig:
    return PlatformDefaultConfig.model_validate({
        'node': {
            'media': {
                'image_models': image or ['gpt-image-2'],
                'image_edit_models': image_edit or ['gpt-image-2'],
                'tts_models': tts if tts is not None else ['qwen3-tts-flash'],
                'stt_models': stt if stt is not None else ['qwen3-asr-flash'],
                'video_models': video or [],
            }
        },
        'agent_runtime': {
            'models': {'main': main, 'fast': fast, 'vision': vision, 'delegation': delegation},
            'model_fallback_pool': fallback_pool or [],
        },
    })


async def _remove_platform_override(db: AsyncSession) -> None:
    """在当前未提交事务内构造“无 PDC 行”场景，退出会话后自动回滚。"""
    await db.execute(
        sa.delete(HasnPlatformDefaultConfig).where(HasnPlatformDefaultConfig.config_key == 'global')
    )
    await db.flush()


async def test_factory_speech_models_match_node_fallback_contract() -> None:
    """云端出厂值必须与节点 Rust 兜底同源，避免 PDC 首次下发造成模型漂移。"""
    media = DEFAULT_PLATFORM_CONFIG['node']['media']
    assert media['tts_models'] == ['qwen3-tts-flash', 'qwen3-tts-instruct-flash']
    assert media['stt_models'] == ['qwen3-asr-flash']


async def test_legacy_media_gateway_defaults_are_normalized_without_mutating_source() -> None:
    """旧配置应补图像编辑链并升级语音链，且不得原地修改数据库读取结果。"""
    raw: dict[str, Any] = {
        'node': {
            'media': {
                'image_models': ['gpt-image-2'],
                'tts_models': ['tts-1', 'tts-1-hd'],
                'stt_models': ['whisper-1'],
                'video_models': [],
            }
        },
        'agent_runtime': {'models': {}},
    }

    normalized = normalize_legacy_media_gateway_defaults(raw)

    assert (
        normalized['node']['media']['image_edit_models']
        == DEFAULT_PLATFORM_CONFIG['node']['media']['image_edit_models']
    )
    assert normalized['node']['media']['tts_models'] == ['qwen3-tts-flash', 'qwen3-tts-instruct-flash']
    assert normalized['node']['media']['stt_models'] == ['qwen3-asr-flash']
    assert raw['node']['media']['tts_models'] == ['tts-1', 'tts-1-hd']
    assert raw['node']['media']['stt_models'] == ['whisper-1']


async def test_custom_speech_gateway_models_are_not_normalized() -> None:
    """运营自定义模型链优先级高于平台迁移规则，不得被默认值升级覆盖。"""
    raw = {
        'node': {
            'media': {
                'image_edit_models': ['custom-image-edit'],
                'tts_models': ['custom-tts', 'tts-1'],
                'stt_models': ['custom-asr'],
            }
        }
    }

    assert normalize_legacy_media_gateway_defaults(raw) == raw


async def test_partial_custom_legacy_speech_chains_return_structured_advisories() -> None:
    """部分自定义链必须保留原值，并逐字段报告仍在使用的旧模型。"""
    raw = {
        'node': {
            'media': {
                'tts_models': ['custom-tts', 'tts-1'],
                'stt_models': ['whisper-1', 'custom-asr'],
                'image_edit_models': ['custom-image-edit'],
            }
        }
    }

    assert normalize_legacy_media_gateway_defaults(raw) == raw
    advisories = collect_media_upgrade_advisories(raw)

    assert [item.model_dump(mode='json') for item in advisories] == [
        {
            'code': 'legacy_speech_gateway_model',
            'field_path': 'node.media.tts_models',
            'legacy_models': ['tts-1'],
            'recommended_models': ['qwen3-tts-flash', 'qwen3-tts-instruct-flash'],
        },
        {
            'code': 'legacy_speech_gateway_model',
            'field_path': 'node.media.stt_models',
            'legacy_models': ['whisper-1'],
            'recommended_models': ['qwen3-asr-flash'],
        },
    ]


async def test_exact_legacy_defaults_auto_upgrade_without_advisory() -> None:
    """完全旧出厂值走自动升级，不再向管理员显示需要手工处理的提示。"""
    raw = {
        'node': {
            'media': {
                'tts_models': ['tts-1', 'tts-1-hd'],
                'stt_models': ['whisper-1'],
            }
        }
    }

    assert collect_media_upgrade_advisories(raw) == []
    normalized = normalize_legacy_media_gateway_defaults(raw)
    assert normalized['node']['media']['tts_models'] == ['qwen3-tts-flash', 'qwen3-tts-instruct-flash']
    assert normalized['node']['media']['stt_models'] == ['qwen3-asr-flash']


async def test_fully_custom_speech_chains_have_no_advisory() -> None:
    """完全自定义且不含旧模型时不得制造升级提示。"""
    raw = {
        'node': {
            'media': {
                'tts_models': ['custom-tts'],
                'stt_models': ['custom-asr'],
            }
        }
    }

    assert collect_media_upgrade_advisories(raw) == []


async def test_partial_legacy_warning_is_deduplicated_by_config_revision(caplog: pytest.LogCaptureFixture) -> None:
    """同一 PDC revision 被重复读取时只记录一次可处理告警。"""
    caplog.set_level('WARNING', logger='backend.app.hasn.service.platform_default_config_service')
    raw = _config(
        tts=['custom-tts-dedupe', 'tts-1'],
        stt=['custom-asr-dedupe', 'whisper-1'],
    ).model_dump(mode='json')
    _warn_media_upgrade_advisories_once.cache_clear()

    advisories = _record_media_upgrade_advisories(raw)
    repeated = _record_media_upgrade_advisories(raw)

    assert advisories == repeated
    assert [item.field_path for item in advisories] == [
        'node.media.tts_models',
        'node.media.stt_models',
    ]
    matching = [record for record in caplog.records if '平台默认配置仍含旧语音网关模型' in record.message]
    assert len(matching) == 1


async def test_partial_legacy_advisories_are_returned_by_admin_service() -> None:
    """Admin 读取与更新响应必须稳定返回部分自定义链升级提示。"""
    async with async_db_session() as db:
        # P3 起 update_config 校验模型名必须在注册表——先把本用例用到的名字登记进去（随事务回滚）。
        config = _config(tts=['custom-tts-response', 'tts-1'], stt=['whisper-1', 'custom-asr-response'])
        await _seed_registry(db, config)
        updated = await svc.update_config(db, config=config, updated_by='pytest')
        loaded = await svc.get_response(db)

    assert updated.upgrade_advisories == loaded.upgrade_advisories
    assert [item.field_path for item in loaded.upgrade_advisories] == [
        'node.media.tts_models',
        'node.media.stt_models',
    ]


async def test_factory_default_when_no_row() -> None:
    async with async_db_session() as db:
        await _remove_platform_override(db)
        cfg, rev = await svc.get_effective_config(db)
        # 无行 → 出厂默认（与 config/default.toml [media] 对齐），revision 稳定可比较。
        assert cfg.node.media.image_models == DEFAULT_PLATFORM_CONFIG['node']['media']['image_models']
        assert (
            cfg.node.media.image_edit_models
            == DEFAULT_PLATFORM_CONFIG['node']['media']['image_edit_models']
        )
        # agnes-image-2.1-flash 只承载 /images/generations，打 /images/edits 上游 404。
        assert 'agnes-image-2.1-flash' not in cfg.node.media.image_edit_models
        # 视频出厂默认 = 网关上实测真出片的模型，且**每项都带模态与方言**：
        # 模态缺失会让 t2v 请求打到 i2v 模型（必失败且仍预扣配额），方言缺失会让阿里系收到
        # `1280x720` 而不是 `720P` 档位（上游直接 InvalidParameter）。
        # 出参已由 pydantic 解析成 VideoModelSpec，与常量里的 dict 逐字段比。
        # 出参形态是 `str | VideoModelSpec` 的联合；出厂默认全是对象形态，逐条 dump 前先断言，
        # 否则 mypy 认为 str 分支没有 model_dump（也确实没有——真出现字符串就是配置退化了）。
        for spec in cfg.node.media.video_models:
            assert isinstance(spec, VideoModelSpec), f'视频模型必须显式声明模态与方言：{spec}'
        # dump 时剔掉未声明的可选项（quality/notes）——常量里只写了 name/modality/dialect，
        # 带上 None 键会让这条断言恒假（基线红，2026-08-02 修）。
        assert [
            {key: value for key, value in spec.model_dump().items() if value is not None}
            for spec in cfg.node.media.video_models
            if isinstance(spec, VideoModelSpec)
        ] == DEFAULT_PLATFORM_CONFIG['node']['media']['video_models']
        assert cfg.node.media.video_models, '视频渠道已开通，出厂默认不应再为空'
        for spec in cfg.node.media.video_models:
            assert not isinstance(spec, str), f'视频模型必须显式声明模态与方言：{spec}'
            assert spec.name and spec.modality and spec.dialect
        _cfg2, rev2 = await svc.get_effective_config(db)
        assert rev == rev2 and rev


async def test_update_persists_video_models_for_node_downlink() -> None:
    async with async_db_session() as db:
        # 运营下发视频模型 → node.media.video_models 落库并回读（daemon 据此覆盖本机 config）。
        config = _config(video=['sora-1', 'kling-1'])
        await _seed_registry(db, config)
        resp = await svc.update_config(db, config=config, updated_by='pytest')
        cfg, rev = await svc.get_effective_config(db)
        assert rev == resp.revision
        assert cfg.node.media.video_models == ['sora-1', 'kling-1']


async def test_update_persists_image_edit_models_separately_from_generation() -> None:
    """图像编辑模型必须独立下发，不能被文生图模型列表覆盖。"""
    async with async_db_session() as db:
        config = _config(
            image=['agnes-image-2.1-flash'],
            image_edit=['gpt-image-2'],
        )
        await _seed_registry(db, config)
        await svc.update_config(db, config=config, updated_by='pytest')
        cfg, _rev = await svc.get_effective_config(db)
        assert cfg.node.media.image_models == ['agnes-image-2.1-flash']
        assert cfg.node.media.image_edit_models == ['gpt-image-2']


async def test_no_film_section_in_pdc_factory_default() -> None:
    """PDC 出厂默认不再含 node.film（FILMCFG-1：应用专属配置迁出到 hasn_app_catalog.config_json）。

    node 只保留跨应用的 media 默认；film 的 5 类模型 + 引擎包 manifest 现由 catalog 承载、
    经 app_configs 聚合下发——见 test_app_configs_aggregated_from_catalog。
    """
    assert 'film' not in DEFAULT_PLATFORM_CONFIG['node']
    async with async_db_session() as db:
        cfg, _rev = await svc.get_effective_config(db)
        # schema 也已无 node.film 字段。
        assert not hasattr(cfg.node, 'film')


async def test_app_configs_aggregated_from_catalog() -> None:
    """catalog.config_json 经 get_effective_config 聚合进 app_configs 下发，改值 bump revision（FILMCFG-1）。

    管理端在 hasn_app_catalog 直接编辑应用配置（如 film 引擎 manifest 内联）→ 下发响应的
    app_configs.<app_id> 即随之变 → compute_revision 涵盖 → revision 变 → daemon 重拉。
    """
    async with async_db_session() as db:
        await ensure_catalog_seeded(db)  # 确保 film 行存在（build_film_app 已注册）
        _cfg_before, rev_before = await svc.get_effective_config(db)
        film_cfg = {
            'models': {'llm': ['gpt-5'], 'vlm': [], 'image_t2i': [], 'image_it2i': [], 'video': ['kling-1']},
            'engine': {
                'version': '1.0.0',
                'packages': {'darwin-arm64': {'url': 'https://cdn/x.zip', 'sha256': 'ab', 'size': 1}},
            },
        }
        await db.execute(sa.update(HasnAppCatalog).where(HasnAppCatalog.app_id == 'film').values(config_json=film_cfg))
        await db.flush()
        cfg, rev_after = await svc.get_effective_config(db)
        # 聚合下发：daemon 从 app_configs.film 读，取代原 PDC node.film。
        assert cfg.app_configs.get('film') == film_cfg
        # catalog 配置变 → revision 变（daemon 比对重拉）。
        assert rev_after != rev_before


async def test_pdc_update_does_not_persist_app_configs() -> None:
    """PUT PDC 不把 app_configs 反向写进 PDC 表（catalog 才是应用配置权威，FILMCFG-1）。

    即使入参误带 app_configs（聚合回传/前端误填），update_config 落库前剥除——PDC 单行只存
    node + agent_runtime，绝不冻结应用配置造成与 catalog 漂移。
    """
    async with async_db_session() as db:
        config = PlatformDefaultConfig.model_validate({
            'node': {
                'media': {
                    'image_models': ['gpt-image-2'],
                    'image_edit_models': ['gpt-image-2'],
                    'tts_models': [],
                    'stt_models': [],
                    'video_models': [],
                }
            },
            'agent_runtime': {'models': {'main': None, 'fast': None, 'vision': None, 'delegation': None}},
            'app_configs': {'film': {'models': {'llm': ['injected-should-be-dropped']}}},
        })
        await _seed_registry(db, config)
        await svc.update_config(db, config=config, updated_by='pytest')
        row = await svc._get_row(db)
        assert row is not None
        assert 'app_configs' not in (row.config_json or {})


async def test_update_changes_revision_and_persists_in_txn() -> None:
    async with async_db_session() as db:
        _, base_rev = await svc.get_effective_config(db)
        config = _config(image=['gpt-image-1'], main='gpt-5')
        await _seed_registry(db, config)
        resp = await svc.update_config(db, config=config, updated_by='pytest')
        assert resp.revision != base_rev
        # 同事务内重读：flush 后可见，配置与 revision 一致（退出回滚不落库）。
        cfg, rev = await svc.get_effective_config(db)
        assert rev == resp.revision
        assert cfg.node.media.image_models == ['gpt-image-1']
        assert cfg.agent_runtime.models.main == 'gpt-5'


async def test_factory_default_fallback_pool_empty() -> None:
    """出厂默认主模型兜底池为空（LLMFAIL）——无兜底，单模型行为不回归。"""
    async with async_db_session() as db:
        await _remove_platform_override(db)
        cfg, _rev = await svc.get_effective_config(db)
        assert cfg.agent_runtime.model_fallback_pool == []
        assert DEFAULT_PLATFORM_CONFIG['agent_runtime']['model_fallback_pool'] == []


async def test_update_persists_model_fallback_pool_for_runtime_downlink() -> None:
    """运营下发主模型兜底池 → agent_runtime.model_fallback_pool 落库并回读，且 bump revision（LLMFAIL）。

    daemon 据此池为每个分身的已解析主模型生成兜底链下发 runtime（剔除主模型自身、去重、保序）。
    """
    async with async_db_session() as db:
        _, base_rev = await svc.get_effective_config(db)
        config = _config(main='gpt-5.5', fallback_pool=['gpt-4o', 'claude-sonnet-4-6'])
        await _seed_registry(db, config)
        resp = await svc.update_config(db, config=config, updated_by='pytest')
        assert resp.revision != base_rev  # 兜底池属 PDC 权威 → 改值 bump revision → daemon 重拉。
        cfg, rev = await svc.get_effective_config(db)
        assert rev == resp.revision
        assert cfg.agent_runtime.model_fallback_pool == ['gpt-4o', 'claude-sonnet-4-6']


async def test_build_effective_runtime_config_coalesce() -> None:
    async with async_db_session() as db:
        config = _config(main='p-main', fast='p-fast', vision='p-vision')
        await _seed_registry(db, config)
        await svc.update_config(db, config=config, updated_by='pytest')
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
        config = _config()
        await _seed_registry(db, config)
        await svc.update_config(db, config=config, updated_by='pytest')
        assert await svc.build_effective_runtime_config(db, None) is None


async def test_build_effective_platform_only_when_raw_none() -> None:
    async with async_db_session() as db:
        config = _config(main='only-platform')
        await _seed_registry(db, config)
        await svc.update_config(db, config=config, updated_by='pytest')
        eff = await svc.build_effective_runtime_config(db, None)
        assert eff is not None and eff['models']['main'] == 'only-platform'
