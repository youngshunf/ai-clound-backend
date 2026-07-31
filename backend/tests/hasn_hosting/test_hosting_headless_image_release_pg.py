"""无头镜像发布登记与解析（H8 · 契约 §7）——零 mock，打真实 PostgreSQL。

重点是「只加不改」：镜像登记不得动 `is_latest`、不得删桌面端资产、不得改批次状态；
`resolve_image` 必须以 digest 为准，缺 digest 宁可拒也不拉一个猜出来的 tag。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_release.model import AppRelease, ReleaseAsset
from backend.app.hasn_release.schema.release import HeadlessImageRequest
from backend.app.hasn_release.service.release_service import release_service
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

VERSION = '0.0.0-hostingtest'
DIGEST = 'sha256:' + 'a' * 64
IMAGE_REF = 'registry.example.com/huanxing/hasn-node:0.0.0-hostingtest'
TARGET = 'headless-linux-amd64'


async def _purge(sess) -> None:
    await sess.execute(
        text(
            'DELETE FROM hasn_release.release_asset WHERE release_id IN '
            '(SELECT id FROM hasn_release.app_release WHERE version = :v)'
        ),
        {'v': VERSION},
    )
    await sess.execute(text('DELETE FROM hasn_release.app_release WHERE version = :v'), {'v': VERSION})
    await sess.commit()


@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(s)
        yield s
    finally:
        await _purge(s)
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


def _req(**over) -> HeadlessImageRequest:
    payload = {
        'version': VERSION,
        'channel': 'stable',
        'platform_target': TARGET,
        'image_ref': IMAGE_REF,
        'image_digest': DIGEST,
        'image_size': 512,
        'min_cloud_contract_version': '1.0.0',
    }
    payload.update(over)
    return HeadlessImageRequest(**payload)


async def test_register_headless_image_upserts_asset(sess) -> None:
    detail = await release_service.register_headless_image(sess, _req())
    await sess.commit()
    assert detail.image_digest == DIGEST
    assert detail.platform_target == TARGET
    assert detail.min_cloud_contract_version == '1.0.0'

    asset = (
        await sess.execute(
            select(ReleaseAsset).where(
                ReleaseAsset.release_id == detail.release_id, ReleaseAsset.asset_kind == 'image'
            )
        )
    ).scalar_one()
    assert asset.download_url == IMAGE_REF
    assert asset.sha256 == DIGEST

    # 幂等重推同一目标只更新那一行，不新增
    await release_service.register_headless_image(sess, _req(image_size=1024))
    await sess.commit()
    assets = (
        await sess.execute(select(ReleaseAsset).where(ReleaseAsset.release_id == detail.release_id))
    ).scalars().all()
    assert len(assets) == 1
    await sess.refresh(assets[0])
    assert assets[0].file_size == 1024


async def test_headless_image_never_steals_latest_pointer(sess) -> None:
    """镜像登记不抢 is_latest：桌面端下载页/updater 的最新指针必须原样不动。"""
    before = (
        await sess.execute(
            select(AppRelease.id).where(AppRelease.channel == 'stable', AppRelease.is_latest.is_(True))
        )
    ).scalars().all()

    detail = await release_service.register_headless_image(sess, _req())
    await sess.commit()

    after = (
        await sess.execute(
            select(AppRelease.id).where(AppRelease.channel == 'stable', AppRelease.is_latest.is_(True))
        )
    ).scalars().all()
    assert set(after) == set(before)

    release = (await sess.execute(select(AppRelease).where(AppRelease.id == detail.release_id))).scalar_one()
    await sess.refresh(release)
    assert release.is_latest is False


async def test_bad_digest_is_rejected(sess) -> None:
    with pytest.raises(errors.RequestError):
        await release_service.register_headless_image(sess, _req(image_digest='not-a-digest'))


async def test_desktop_platform_target_is_rejected(sess) -> None:
    """写错目标就是往桌面端表里塞脏行，必须拒。"""
    with pytest.raises(errors.RequestError):
        await release_service.register_headless_image(sess, _req(platform_target='darwin-aarch64'))


async def test_resolve_image_picks_published_headless_asset(sess) -> None:
    await release_service.register_headless_image(sess, _req())
    await sess.commit()

    image_ref, digest, version = await cloud_node_service.resolve_image(sess, platform_target=TARGET)
    assert digest == DIGEST
    assert image_ref == IMAGE_REF
    assert version


async def test_resolve_image_raises_when_no_published_image(sess) -> None:
    with pytest.raises(errors.NotFoundError):
        await cloud_node_service.resolve_image(sess, platform_target='headless-linux-arm64')
