"""桌面端发布 release_service 真实 PG 测试（零 mock）。

覆盖（设计事实源 docs/桌面端发布与自动更新/00 §3–§5）：
- publish：建版本 + 各平台资产、is_latest 置位、get_latest 返回 installer；
- updater 资产缺 minisign 签名 → 拒收（RequestError）；
- 非 https CDN 直链 → 拒收；
- 发新版本：同 channel 旧版 is_latest 落 false；
- build_updater_manifest：更新时返回带 signature+url 的 manifest，无更新/同版本返回 None；
- resolve_download：累加 download_count 并返回 CDN 直链；
- set_latest：把历史版本重新置最新（回滚）；
- ci_callback：source=github + 回填关联 build 状态 success；
- delete：级联删资产。

service 内部 commit（非 flush/rollback），故测试用 99.x 版本命名空间 + teardown 清理，
不污染真实版本行。本地 PostgreSQL 不可达则 skip。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_release.model import AppRelease, ReleaseAsset, ReleaseBuild
from backend.app.hasn_release.schema.release import (
    CiCallbackRequest,
    GithubBuildRequest,
    PublishReleaseRequest,
    ReleaseAssetInput,
)
from backend.app.hasn_release.service.release_service import release_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

# 测试版本命名空间（99.x，teardown 按前缀清理，避免污染真实版本）
_TEST_PREFIX = '99.'
_CDN = 'https://cdn.test.example/astra'


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    maker = async_sessionmaker(engine, expire_on_commit=False)
    sess = maker()
    try:
        yield sess
    finally:
        # 清理测试行（99.x 版本 + test/* 构建 ref）；资产经 FK CASCADE 随版本删
        async with maker() as cleanup:
            ids = (
                await cleanup.execute(
                    select(AppRelease.id).where(AppRelease.version.like(f'{_TEST_PREFIX}%'))
                )
            ).scalars().all()
            if ids:
                await cleanup.execute(delete(ReleaseAsset).where(ReleaseAsset.release_id.in_(ids)))
                await cleanup.execute(delete(AppRelease).where(AppRelease.id.in_(ids)))
            await cleanup.execute(delete(ReleaseBuild).where(ReleaseBuild.ref.like('test/%')))
            await cleanup.commit()
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _asset(target: str, kind: str, *, sig: str | None = None, url: str | None = None) -> ReleaseAssetInput:
    return ReleaseAssetInput(
        platform_target=target,
        asset_kind=kind,
        download_url=url or f'{_CDN}/{target}-{kind}.bin',
        file_name=f'{target}-{kind}.bin',
        file_size=1234,
        sha256='a' * 64,
        signature=sig,
    )


def _publish_req(version: str, *, channel: str = 'stable', set_latest: bool = True) -> PublishReleaseRequest:
    return PublishReleaseRequest(
        version=version,
        channel=channel,
        release_notes_md=f'# {version}\n更新内容',
        source='manual',
        set_latest=set_latest,
        assets=[
            _asset('darwin-aarch64', 'installer', url=f'{_CDN}/{version}/arm64.dmg'),
            _asset('darwin-aarch64', 'updater', sig='SIG_ARM64', url=f'{_CDN}/{version}/arm64.tar.gz'),
            _asset('darwin-x86_64', 'installer', url=f'{_CDN}/{version}/x64.dmg'),
            _asset('darwin-x86_64', 'updater', sig='SIG_X64', url=f'{_CDN}/{version}/x64.tar.gz'),
        ],
    )


async def test_publish_creates_release_and_assets_and_latest(session):
    detail = await release_service.publish(session, _publish_req('99.1.0'), source='manual')
    assert detail.version == '99.1.0'
    assert detail.is_latest is True
    assert detail.status == 'published'
    # 4 个资产（2 平台 × installer/updater）
    assert len(detail.assets) == 4

    latest = await release_service.get_latest(session, channel='stable')
    assert latest.version == '99.1.0'
    # 下载页只暴露 installer（两平台）
    assert set(latest.installers.keys()) == {'darwin-aarch64', 'darwin-x86_64'}
    assert latest.installers['darwin-aarch64'].download_url.endswith('arm64.dmg')


async def test_updater_asset_requires_signature(session):
    req = _publish_req('99.1.1')
    # 抹掉一个 updater 的签名 → 应拒收
    req.assets[1].signature = None
    with pytest.raises(errors.RequestError):
        await release_service.publish(session, req, source='manual')


async def test_asset_url_must_be_https(session):
    req = _publish_req('99.1.2')
    req.assets[0].download_url = 'http://cdn.test.example/insecure.dmg'
    with pytest.raises(errors.RequestError):
        await release_service.publish(session, req, source='manual')


async def test_publish_new_version_flips_old_latest(session):
    await release_service.publish(session, _publish_req('99.2.0'), source='manual')
    await release_service.publish(session, _publish_req('99.3.0'), source='manual')

    latest = await release_service.get_latest(session, channel='stable')
    assert latest.version == '99.3.0'

    # 旧版 is_latest 应落 false
    old = (
        await session.execute(select(AppRelease).where(AppRelease.version == '99.2.0'))
    ).scalar_one()
    assert old.is_latest is False


async def test_updater_manifest_offers_update_when_newer(session):
    await release_service.publish(session, _publish_req('99.4.0'), source='manual')

    # 客户端在 99.3.0 → 有更新
    manifest = await release_service.build_updater_manifest(
        session, target='darwin', arch='aarch64', current_version='99.3.0', channel='stable'
    )
    assert manifest is not None
    assert manifest.version == '99.4.0'
    entry = manifest.platforms['darwin-aarch64']
    assert entry.signature == 'SIG_ARM64'
    assert entry.url.endswith('arm64.tar.gz')

    # 客户端已是 99.4.0 → 无更新（None → 端点 204）
    none_manifest = await release_service.build_updater_manifest(
        session, target='darwin', arch='aarch64', current_version='99.4.0', channel='stable'
    )
    assert none_manifest is None

    # 客户端更新（99.9.0）→ 也无更新
    newer = await release_service.build_updater_manifest(
        session, target='darwin', arch='aarch64', current_version='99.9.0', channel='stable'
    )
    assert newer is None


async def test_resolve_download_increments_count(session):
    detail = await release_service.publish(session, _publish_req('99.5.0'), source='manual')
    installer = next(a for a in detail.assets if a.asset_kind == 'installer')
    assert installer.download_count == 0

    url = await release_service.resolve_download(session, installer.id)
    assert url == installer.download_url

    row = (
        await session.execute(select(ReleaseAsset).where(ReleaseAsset.id == installer.id))
    ).scalar_one()
    assert row.download_count == 1


async def test_set_latest_rolls_back_to_older(session):
    await release_service.publish(session, _publish_req('99.6.0'), source='manual')
    v7 = await release_service.publish(session, _publish_req('99.7.0'), source='manual')
    assert (await release_service.get_latest(session, channel='stable')).version == '99.7.0'

    # 回滚：把 99.6.0 重新置最新
    v6 = (
        await session.execute(select(AppRelease).where(AppRelease.version == '99.6.0'))
    ).scalar_one()
    await release_service.set_latest(session, v6.id, channel='stable')

    latest = await release_service.get_latest(session, channel='stable')
    assert latest.version == '99.6.0'
    # 原最新落 false
    v7_row = (
        await session.execute(select(AppRelease).where(AppRelease.id == v7.id))
    ).scalar_one()
    assert v7_row.is_latest is False


async def test_ci_callback_sets_source_github_and_updates_build(session):
    # 先造一个 queued 构建
    build = ReleaseBuild(
        ref='test/main', channel='stable', status='queued', triggered_by='pytest'
    )
    session.add(build)
    await session.flush()
    await session.commit()
    build_id = build.id

    req = CiCallbackRequest(
        version='99.8.0',
        channel='stable',
        source='github',
        github_run_id='run-123',
        set_latest=True,
        build_id=build_id,
        assets=_publish_req('99.8.0').assets,
    )
    detail = await release_service.ci_callback(session, req)
    assert detail.source == 'github'
    assert detail.github_run_id == 'run-123'

    updated = (
        await session.execute(select(ReleaseBuild).where(ReleaseBuild.id == build_id))
    ).scalar_one()
    assert updated.status == 'success'
    assert updated.version == '99.8.0'


async def test_github_build_without_config_queues_with_reason(session):
    # 未配置 RELEASE_GITHUB_TOKEN 时（默认空）：落 queued 行 + 写明原因，不 fake 成功
    build = await release_service.trigger_github_build(
        session, GithubBuildRequest(ref='test/main', channel='stable'), actor='pytest'
    )
    assert build.status == 'queued'
    assert build.error_message and '未配置' in build.error_message


async def test_delete_cascades_assets(session):
    detail = await release_service.publish(session, _publish_req('99.9.0'), source='manual')
    release_id = detail.id
    asset_ids = [a.id for a in detail.assets]
    assert asset_ids

    await release_service.delete(session, release_id)

    remaining = (
        await session.execute(select(ReleaseAsset).where(ReleaseAsset.release_id == release_id))
    ).scalars().all()
    assert remaining == []
