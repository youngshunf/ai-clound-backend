"""Publish bundle-zip 物化异步化契约测试（2026-08-29，真实 PG；成功路径连真实对象存储）。

背景：发布接口曾在请求内同步做 bundle-zip 物化（读 zip + 逐对象 PUT 对象存储），实测 38s+
超过 daemon 写死的 30s 上游超时。现在 bundle-zip 落 pending revision 立即返回，Celery worker
物化完成后翻 current_revision_id 指针。本文件钉住这条边界：

① create/update 的 bundle-zip 分支只落 pending、不翻指针、不碰对象存储（请求必须秒回）；
② single-html 等无 fan-out 的 runtime 维持同步物化 + 立即 ready（不许被异步化误伤）；
③ content_hash 去重按物化状态三分（ready 翻指针 / pending 复用不动 / failed 重置重派）；
④ materialize_revision：成功回写 manifest.files 并按 seq 翻指针；业务失败落 failed + 文案、
   不翻指针、幂等可重入；站点已删落终态不再被 sweep；
⑤ sweep 只捞过宽限期的滞留 pending。
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.app.hasn_publish.model.revision import Revision
from backend.app.hasn_publish.model.site import Site
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_db_session
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """真实 PostgreSQL 会话；用例结束回滚，不污染开发库。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


def _tag() -> str:
    return uuid.uuid4().hex[:10]


async def _create_bundle_site(db: AsyncSession, owner: str, *, content_hash: str = '') -> dict:
    """bundle-zip 创建（asset 此刻不被触碰——物化已异步化，假 asset_id 也能落 pending）。"""
    return await publish_service.create_site(
        db,
        owner_id=owner,
        title='异步发布测试',
        asset_id=f'asset_{_tag()}',
        runtime='bundle-zip',
        content_hash=content_hash,
        manifest_json={'entry': 'index.html', 'assets': []},
    )


@pytest.mark.asyncio
async def test_create_bundle_zip_is_pending_and_defers_pointer(session) -> None:
    """bundle-zip create：pending + 不翻指针 + manifest 原样（无 files），响应秒回不碰存储。"""
    owner = f'h_pub_async_{_tag()}'
    created = await _create_bundle_site(session, owner)

    assert created['revision']['materialize_status'] == 'pending'
    assert created['revision']['materialize_error'] is None
    assert created['site']['current_revision_id'] is None, '物化完成前不许翻指针（翻转是 worker 的职责）'
    assert created['revision']['manifest_json'] == {'entry': 'index.html', 'assets': []}, (
        'pending 期间 manifest 必须保持打包侧原样，files 由物化任务回写'
    )


@pytest.mark.asyncio
async def test_create_single_html_stays_sync_ready(session) -> None:
    """single-html 无 fan-out：维持同步物化 + 立即 ready + 立即翻指针（异步化不许误伤快路径）。"""
    owner = f'h_pub_async_{_tag()}'
    created = await publish_service.create_site(
        session, owner_id=owner, title='同步路径回归', asset_id=f'asset_{_tag()}', runtime='single-html'
    )

    assert created['revision']['materialize_status'] == 'ready'
    assert created['site']['current_revision_id'] == created['revision']['id']


@pytest.mark.asyncio
async def test_update_bundle_zip_keeps_old_pointer_until_ready(session) -> None:
    """bundle-zip update：新 revision pending，指针留在旧 ready 版本——旧内容服务到最后一刻。"""
    owner = f'h_pub_async_{_tag()}'
    created = await publish_service.create_site(
        session,
        owner_id=owner,
        title='先上一个能看的',
        asset_id=f'asset_{_tag()}',
        runtime='single-html',
        content_hash=hashlib.sha256(b'v1').hexdigest(),
    )
    site_id = created['site']['id']
    rev1_id = created['revision']['id']

    updated = await publish_service.update_site(
        session,
        owner_id=owner,
        site_id=site_id,
        asset_id=f'asset_{_tag()}',
        runtime='bundle-zip',
        content_hash=hashlib.sha256(b'v2').hexdigest(),
        manifest_json={'entry': 'index.html', 'assets': []},
    )

    assert updated['revision']['materialize_status'] == 'pending'
    assert updated['revision']['seq'] == 2
    assert updated['site']['current_revision_id'] == rev1_id, 'pending 期间指针必须留在旧 ready 版本'
    assert updated['site']['status'] == 'active'


@pytest.mark.asyncio
async def test_update_dedup_pending_reuses_without_new_revision(session) -> None:
    """同 hash 撞 pending：复用且不新起 revision、不动指针——重试风暴不再放大版本数。"""
    owner = f'h_pub_async_{_tag()}'
    content_hash = hashlib.sha256(b'storm').hexdigest()
    created = await publish_service.create_site(
        session,
        owner_id=owner,
        title='重试风暴',
        asset_id=f'asset_{_tag()}',
        runtime='bundle-zip',
        content_hash=content_hash,
        manifest_json={'entry': 'index.html', 'assets': []},
    )
    site_id = created['site']['id']

    retried = await publish_service.update_site(
        session,
        owner_id=owner,
        site_id=site_id,
        asset_id=created['revision']['asset_id'],
        runtime='bundle-zip',
        content_hash=content_hash,
        manifest_json={'entry': 'index.html', 'assets': []},
    )

    assert retried['reused'] is True
    assert retried['revision']['id'] == created['revision']['id'], '同 hash 撞 pending 必须复用原 revision'
    assert retried['revision']['materialize_status'] == 'pending'
    assert retried['site']['current_revision_id'] is None, '复用 pending 不许顺手翻指针'
    count = (
        await session.execute(sa.select(sa.func.count()).select_from(Revision).where(Revision.site_id == site_id))
    ).scalar_one()
    assert count == 1, '重试不得新起 revision'


@pytest.mark.asyncio
async def test_update_dedup_failed_resets_to_pending(session) -> None:
    """同 hash 撞 failed：重置回 pending 等重派（诚实重试），错误文案清空，不另起版本。"""
    owner = f'h_pub_async_{_tag()}'
    content_hash = hashlib.sha256(b'retry-me').hexdigest()
    created = await _create_bundle_site(session, owner, content_hash=content_hash)
    site_id = created['site']['id']
    rev_id = created['revision']['id']

    # 直接落 failed（等价于物化任务业务失败后的状态）
    rev = await session.get(Revision, rev_id)
    rev.materialize_status = 'failed'
    rev.materialize_error = '发布内容里有图片已失效或未同步到云端'
    await session.flush()

    retried = await publish_service.update_site(
        session,
        owner_id=owner,
        site_id=site_id,
        asset_id=created['revision']['asset_id'],
        runtime='bundle-zip',
        content_hash=content_hash,
        manifest_json={'entry': 'index.html', 'assets': []},
    )

    assert retried['reused'] is True
    assert retried['revision']['id'] == rev_id
    assert retried['revision']['materialize_status'] == 'pending', 'failed 被同内容重试必须重置回 pending'
    assert retried['revision']['materialize_error'] is None, '重置后旧错误文案必须清空'


@pytest.mark.asyncio
async def test_materialize_business_failure_marks_failed_and_skips_pointer(session) -> None:
    """物化业务失败（制品不存在）：落 failed + 主人可读文案，不翻指针；二次执行幂等跳过。"""
    owner = f'h_pub_async_{_tag()}'
    created = await _create_bundle_site(session, owner)
    site_id = created['site']['id']
    rev_id = created['revision']['id']

    result = await publish_service.materialize_revision(session, revision_id=rev_id)

    assert result.startswith('failed:'), f'业务失败必须如实返回 failed，得到 {result}'
    rev = await session.get(Revision, rev_id)
    assert rev.materialize_status == 'failed'
    assert rev.materialize_error, 'failed 必须带主人可读原因（零 fake）'
    site = await session.get(Site, site_id)
    assert site.current_revision_id is None, '失败的 revision 不许翻指针'

    again = await publish_service.materialize_revision(session, revision_id=rev_id)
    assert again == 'skip:failed', '终态 revision 重入必须幂等跳过'


@pytest.mark.asyncio
async def test_materialize_deleted_site_marks_terminal(session) -> None:
    """站点在物化期间被删：落 failed 终态（否则每分钟 sweep 会反复捞起一具尸体）。"""
    owner = f'h_pub_async_{_tag()}'
    created = await _create_bundle_site(session, owner)
    site_id = created['site']['id']
    rev_id = created['revision']['id']

    await publish_service.delete_site(session, owner_id=owner, site_id=site_id)
    result = await publish_service.materialize_revision(session, revision_id=rev_id)

    assert result == 'failed:site-deleted'
    rev = await session.get(Revision, rev_id)
    assert rev.materialize_status == 'failed'


@pytest.mark.asyncio
async def test_find_stuck_pending_respects_grace_state_and_site(session) -> None:
    """sweep 查询面：只捞「过宽限期的 pending + 站点未删」；新 pending / ready / 删站一律不捞。"""
    owner = f'h_pub_async_{_tag()}'
    stuck = await _create_bundle_site(session, owner)
    stuck_rev_id = stuck['revision']['id']
    # 把 created_time 拨回宽限期之前，模拟滞留
    await session.execute(
        sa.update(Revision)
        .where(Revision.id == stuck_rev_id)
        .values(created_time=timezone.now() - timedelta(minutes=10))
    )

    fresh = await _create_bundle_site(session, owner)  # 新 pending：正常在途，不许捞
    ready = await publish_service.create_site(
        session, owner_id=owner, title='已就绪', asset_id=f'asset_{_tag()}', runtime='single-html'
    )
    deleted = await _create_bundle_site(session, owner)
    await session.execute(
        sa.update(Revision)
        .where(Revision.id == deleted['revision']['id'])
        .values(created_time=timezone.now() - timedelta(minutes=10))
    )
    await publish_service.delete_site(session, owner_id=owner, site_id=deleted['site']['id'])
    await session.flush()

    ids = await publish_service.find_stuck_pending_materializations(session)

    assert stuck_rev_id in ids
    assert fresh['revision']['id'] not in ids, '宽限期内的正常 pending 不许捞'
    assert ready['revision']['id'] not in ids, 'ready 不许捞'
    assert deleted['revision']['id'] not in ids, '站点已删的 pending 不许捞'


# ---------------- 成功路径：真实 PG + 真实对象存储 ----------------


def _bundle_zip_bytes() -> bytes:
    """最小合法 bundle：index.html + 一个 assets 子文件。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('index.html', '<!doctype html><title>async ok</title>')
        archive.writestr('assets/pic.png', b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    return buf.getvalue()


async def _seed_owner_and_quota(suffix: str) -> SimpleNamespace:
    """上传真实资产的前置：owner 身份行 + 存储配额账户（committed，用例结束清掉）。"""
    owner = f'h_pub_async_{suffix}'
    user_id = 970_000_000 + int(suffix[:6], 16) % 20_000_000
    async with async_db_session.begin() as db:
        await db.execute(
            sa.text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star, :user_id, :nickname, 'active', '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {'owner': owner, 'star': f'pa{user_id}', 'user_id': user_id, 'nickname': f'异步发布测试_{suffix}'},
        )
        await db.execute(
            sa.text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 104857600, 0, 0, 'admin_override', 'publish-async-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
    return SimpleNamespace(owner=owner, publish_keys=[])


async def _cleanup_owner(ctx: SimpleNamespace) -> None:
    """清掉真实对象（上传的 zip + 物化出的 publish/* 子对象）与 committed 行。"""
    async with async_db_session() as db:
        objects = (
            await db.execute(
                sa.text('SELECT storage_id, object_key FROM hasn_storage_objects WHERE owner_hasn_id = :owner'),
                {'owner': ctx.owner},
            )
        ).mappings().all()
        for obj in objects:
            await StorageService.delete_object(db, storage_id=int(obj['storage_id']), object_key=str(obj['object_key']))
        if ctx.publish_keys and objects:
            storage_id = int(objects[0]['storage_id'])
            for key in ctx.publish_keys:
                await StorageService.delete_object(db, storage_id=storage_id, object_key=key)

    async with async_db_session.begin() as db:
        await db.execute(
            sa.text('DELETE FROM hasn_artifact_registration_outbox WHERE owner_hasn_id = :owner'), {'owner': ctx.owner}
        )
        await db.execute(
            sa.text(
                """
                DELETE FROM hasn_artifact_contributions
                WHERE artifact_id IN (SELECT artifact_id FROM hasn_artifacts WHERE owner_hasn_id = :owner)
                """
            ),
            {'owner': ctx.owner},
        )
        await db.execute(sa.text('DELETE FROM hasn_artifacts WHERE owner_hasn_id = :owner'), {'owner': ctx.owner})
        for table in (
            'hasn_storage_entries',
            'hasn_asset_bindings',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(sa.text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'), {'owner': ctx.owner})  # noqa: S608
        await db.execute(sa.text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': ctx.owner})


@pytest_asyncio.fixture
async def storage_owner() -> AsyncIterator[SimpleNamespace]:
    ctx = await _seed_owner_and_quota(_tag())
    try:
        yield ctx
    finally:
        await _cleanup_owner(ctx)


@pytest.mark.asyncio
async def test_materialize_bundle_zip_success_writes_files_and_flips_pointer(storage_owner) -> None:
    """成功路径全链路：真 zip 上云 → create 落 pending → 物化解包回写 files → ready + 翻指针。

    site/revision 行走在回滚会话里（不污染库）；资产与对象存储走真实通道（用例结束按 key 清）。
    """
    owner = storage_owner.owner
    payload = _bundle_zip_bytes()
    stored = await OwnerStorageService(async_db_session).upload_bytes(
        owner_hasn_id=owner,
        data=payload,
        filename='bundle.zip',
        mime='application/zip',
        category='user_upload',
        source_app='publish_async_test',
        idempotency_key=f'publish-async-{hashlib.sha256(payload).hexdigest()[:16]}',
    )

    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        created = await publish_service.create_site(
            db,
            owner_id=owner,
            title='真物化',
            asset_id=stored.asset_id,
            runtime='bundle-zip',
            manifest_json={'entry': 'index.html', 'assets': []},
        )
        site_id = created['site']['id']
        rev_id = created['revision']['id']
        assert created['revision']['materialize_status'] == 'pending'

        result = await publish_service.materialize_revision(db, revision_id=rev_id)

        assert result == 'ready', f'真实 zip 物化必须成功，得到 {result}'
        rev = await db.get(Revision, rev_id)
        assert rev.materialize_status == 'ready'
        files = (rev.manifest_json or {}).get('files') or {}
        assert set(files) == {'index.html', 'assets/pic.png'}, '解包产物必须逐对象登记进 manifest.files'
        expected_prefix = f'owners/{owner}/publish/{stored.asset_id}/'
        for name, entry in files.items():
            assert entry['object_key'] == f'{expected_prefix}{name}'
            storage_owner.publish_keys.append(entry['object_key'])
        site = await db.get(Site, site_id)
        assert site.current_revision_id == rev_id, 'ready 后指针必须翻到本 revision'

        again = await publish_service.materialize_revision(db, revision_id=rev_id)
        assert again == 'skip:ready', 'ready 后重入必须幂等跳过'
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()
