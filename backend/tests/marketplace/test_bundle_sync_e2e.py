"""hub bundles/ 同步落库 skill_pack 进程内 E2E（实施/91 B3.3，真实 PG，零 mock）。

直接驱动 GitHubSyncService._sync_bundles（隔离 git clone + LLM 翻译），local_path 指向临时
hub 子集（bundles/*/bundle.yaml + common-bundles.yaml），断言：
  1) 合法 bundle → 真落 marketplace_template(template_type='skill_pack', is_official, is_common)
     + marketplace_template_version（bundle_slug/command_key/hermes_yaml/content_hash）；
     hermes_yaml 可 safe_load 回 dict、剔除了 marketplace 维度键。
  2) 改 bundle 内容 → content_hash 变（重扫即覆盖最新版本）。
  3) common-bundles.yaml 命中 → is_common=true；移出 → 重扫落 false。
  4) 非法 bundle（skills 空 / slug 不自洽）→ 不落库 + 计入 failed。

事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import yaml

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.service.github_sync_service import GitHubSyncService
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _seed_skill(session, namespace: str, slug: str) -> None:
    """落一条已发布公开技能（实施/92：技能包成员落库时按完整 id 校验已发布公开才放行）。"""
    skill_id = f'{namespace}/{slug}'
    await session.execute(delete(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id))
    session.add(
        MarketplaceSkill(skill_id=skill_id, namespace=namespace, slug=slug, name=slug, status='published', visibility='public')
    )
    await session.flush()


def _write_bundle(root, slug: str, *, name: str | None = None, display_name: str | None = None,
                  skills: list[str] | None = None,
                  instruction: str = '先跑测试，再 code review。') -> None:
    bdir = root / 'bundles' / slug
    bdir.mkdir(parents=True, exist_ok=True)
    spec = {
        'name': name if name is not None else slug,
        'description': f'{slug} 技能包',
        'skills': skills if skills is not None else ['huanxing/developer/code-review'],
        'instruction': instruction,
    }
    if display_name is not None:
        spec['display_name'] = display_name
    (bdir / 'bundle.yaml').write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding='utf-8')


@pytest_asyncio.fixture
async def e2e(tmp_path):
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    # 技能包成员落库时按完整 id 校验已发布公开（实施/92 D-NAMING）；生产中 _sync_skill 先于
    # _sync_bundles 已落这些成员，测试里显式 seed 等价前置条件。
    await _seed_skill(session, 'huanxing/developer', 'code-review')
    await _seed_skill(session, 'huanxing/productivity', 'tdd')
    service = GitHubSyncService()
    service.local_path = str(tmp_path)

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.session = session
    ctx.service = service
    ctx.root = tmp_path
    try:
        yield ctx
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _fetch_pack(session, template_id: str) -> dict | None:
    row = (
        await session.execute(
            text(
                '''
                SELECT t.template_type, t.is_official, t.is_common, t.namespace,
                       v.bundle_slug, v.command_key, v.hermes_yaml, v.content_hash, v.file_hash, v.is_latest
                FROM hasn_marketplace.marketplace_template t
                JOIN hasn_marketplace.marketplace_template_version v ON v.template_id = t.template_id AND v.is_latest
                WHERE t.template_id = :tid
                '''
            ),
            {'tid': template_id},
        )
    ).mappings().one_or_none()
    return dict(row) if row else None


async def test_sync_bundles_persists_skill_pack_contract(e2e):
    slug = f'backend-dev-{_tag()}'
    tid = f'huanxing/{slug}'
    _write_bundle(e2e.root, slug, skills=['huanxing/developer/code-review', 'huanxing/productivity/tdd'])
    # 标记为公共包
    (e2e.root / 'common-bundles.yaml').write_text(
        yaml.safe_dump({'bundles': [slug]}, allow_unicode=True), encoding='utf-8'
    )

    synced, failed, errors = await e2e.service._sync_bundles(e2e.session)
    assert (synced, failed) == (1, 0), (synced, failed, errors)

    row = await _fetch_pack(e2e.session, tid)
    assert row is not None, '技能包未落库'
    assert row['template_type'] == 'skill_pack'
    assert row['is_official'] is True
    assert row['is_common'] is True              # 命中 common-bundles.yaml
    assert row['bundle_slug'] == slug
    assert row['command_key'] == f'/{slug}'
    assert row['content_hash'].startswith('sha256:')
    # hermes_yaml 可 safe_load 回 dict，含两个成员，剔除了 marketplace 维度键
    spec = yaml.safe_load(row['hermes_yaml'])
    assert isinstance(spec, dict)
    assert spec['skills'] == ['huanxing/developer/code-review', 'huanxing/productivity/tdd']
    assert 'is_common' not in spec and 'is_official' not in spec and 'version' not in spec


async def test_sync_bundles_content_change_bumps_hash_and_common_toggle(e2e):
    slug = f'research-{_tag()}'
    tid = f'huanxing/{slug}'
    _write_bundle(e2e.root, slug, instruction='第一版指令')
    (e2e.root / 'common-bundles.yaml').write_text(
        yaml.safe_dump({'bundles': [slug]}, allow_unicode=True), encoding='utf-8'
    )

    synced, failed, _ = await e2e.service._sync_bundles(e2e.session)
    assert (synced, failed) == (1, 0)
    first = await _fetch_pack(e2e.session, tid)
    assert first['is_common'] is True
    hash1 = first['content_hash']

    # 改内容 + 移出公共集合 → content_hash 变、is_common 落 false
    _write_bundle(e2e.root, slug, instruction='第二版指令——内容已变')
    (e2e.root / 'common-bundles.yaml').write_text(
        yaml.safe_dump({'bundles': []}, allow_unicode=True), encoding='utf-8'
    )
    synced2, failed2, _ = await e2e.service._sync_bundles(e2e.session)
    assert (synced2, failed2) == (1, 0)
    second = await _fetch_pack(e2e.session, tid)
    assert second['content_hash'] != hash1          # 内容变 → hash 变
    assert second['is_common'] is False             # 移出公共集合 → 重扫落 false


async def test_sync_bundle_display_name_maps_to_name(e2e):
    """bundle.yaml 的中文 display_name 落库 marketplace_template.name（卡片展示用）；

    slug 标识仍存 bundle_slug/command_key；hermes_yaml 保持纯净（display_name 属 marketplace
    维度不进 hermes 包，hermes 的 name 仍是 slug 供命令匹配）。
    （icon.svg → 公共桶 icon_url 的上传路径走真实 S3，由活体同步 + test_icon_public_storage 覆盖，
    此处不打 S3 保持单测稳定。）
    """
    slug = f'pack-dn-{_tag()}'
    _write_bundle(e2e.root, slug, display_name='测试中文名')
    synced, failed, errors = await e2e.service._sync_bundles(e2e.session)
    assert (synced, failed) == (1, 0), (synced, failed, errors)

    name = (
        await e2e.session.execute(
            text('SELECT name FROM hasn_marketplace.marketplace_template WHERE template_id = :tid'),
            {'tid': f'huanxing/{slug}'},
        )
    ).scalar()
    assert name == '测试中文名'  # display_name → DB name

    pack = await _fetch_pack(e2e.session, f'huanxing/{slug}')
    assert pack is not None and pack['bundle_slug'] == slug  # slug 标识不受影响
    spec = yaml.safe_load(pack['hermes_yaml'])
    assert 'display_name' not in spec  # marketplace 维度不进 hermes 包
    assert spec['name'] == slug  # hermes name 仍是 slug


async def test_sync_bundles_skips_invalid(e2e):
    good = f'good-{_tag()}'
    empty_skills = f'emptyskills-{_tag()}'
    mismatch = f'mismatch-{_tag()}'
    _write_bundle(e2e.root, good)
    _write_bundle(e2e.root, empty_skills, skills=[])                  # skills 空 → 同步期跳过（不落库、不计 failed）
    _write_bundle(e2e.root, mismatch, name='完全不同的名字')          # name 归一化 != 目录名 → 跳过

    synced, failed, _ = await e2e.service._sync_bundles(e2e.session)
    # 非法 bundle 在 _parse_bundle_yaml 内 log.error 跳过、不进 records，故只有 1 个合法包被同步
    assert synced == 1
    assert await _fetch_pack(e2e.session, f'huanxing/{good}') is not None
    assert await _fetch_pack(e2e.session, f'huanxing/{empty_skills}') is None
    assert await _fetch_pack(e2e.session, f'huanxing/{mismatch}') is None
