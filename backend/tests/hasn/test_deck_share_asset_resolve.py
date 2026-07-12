"""被分享者打开共享 deck 时图片可解析（跨 owner 资产授权）真实 PG 测试（零 mock）。

复现并守卫「把 PPT 分享给好友，好友打开看不到图片」的根因修复：deck 页内 `hasn://asset/{id}`
私有图片，对被分享者（viewer）既非 owner、也无会话授权，此前 resolve 一律跳过 → 破图。
修复后：resolve 端点据 `resource_ref=deck:{id}` 经**资产投影门**（G6 registry 第二消费者，doc32 §14）
按 deck ACL 收集该 deck 引用的资产作 extra_readable，只放行「该 deck 确实引用 ∩ requester 有 viewer+」的资产。

覆盖两层（S6 收编后）：
1. DeckResourceAdapter.collect_asset_ids —— 纯收集（无判权），穷尽 cover + 每页 thumb + html 内嵌引用。
2. asset_projection.readable_asset_ids —— 按 deck ACL × 请求集求交（deck 三场景 + 越权 asset 丢弃）；
   HasnAssetService.resolve(extra_readable_asset_ids=...) —— 私有资产经此额外放行签发 URL。

插入隔离测试行 → flush（不 commit）→ 断言 → rollback。签名网络边界用 fake，不打真实 S3/Redis。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service import hasn_asset_service as svc_mod
from backend.app.hasn.service.authz import Subject, asset_projection
from backend.app.hasn.service.hasn_asset_service import HasnAssetService
from backend.app.hasn_deck.service.deck_service import deck_service
from backend.app.hasn_deck.service.resource_adapter import DeckResourceAdapter
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.plugin.s3.service.storage_service import ObjectRef

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _fake_sign(_db, *, items, expires_in=3600) -> dict[tuple[str, ...], str]:  # noqa: RUF029
    # 保持 async：monkeypatch 到 signed_urls_cached（异步），调用方要 await，故须返回协程。
    return {it: f'https://signed/{it[1]}?e={expires_in}' for it in items}


async def test_collect_and_gate_intersects_only_deck_assets_for_viewer(session) -> None:
    """collect_asset_ids 穷尽收集；资产投影门按 deck ACL × 请求集求交（三场景 + 越权 asset 丢弃）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    cover_id = f'as_cover_{tag}'
    thumb_id = f'as_thumb_{tag}'
    inline1 = f'as_in1_{tag}'
    inline2 = f'as_in2_{tag}'
    stranger = f'as_evil_{tag}'  # 不属于该 deck 的越权 asset

    deck = await deck_service.create_deck(session, owner_id=a.hasn_id, title='带图 PPT')
    deck_id = deck['id']
    await deck_service.update_deck(session, subject=a, deck_id=deck_id, fields={'cover_asset_id': cover_id})
    html = f'<section><img src="hasn://asset/{inline1}"><img src="hasn://asset/{inline2}"></section>'
    page = await deck_service.create_page(session, subject=a, deck_id=deck_id, position=0, html=html)
    await deck_service.update_page(session, subject=a, page_id=page['id'], fields={'thumb_asset_id': thumb_id})

    ref = f'deck:{deck_id}'
    deck_assets = {cover_id, thumb_id, inline1, inline2}
    requested = deck_assets | {stranger}  # 请求集混入一个越权 asset

    # ① collect_asset_ids：纯收集（无判权），穷尽 cover + thumb + inline（⊇ 该 deck 引用的私有资产）
    collected = await DeckResourceAdapter().collect_asset_ids(session, str(deck_id))
    assert collected == deck_assets

    # ② 未分享：B 无 viewer → 门返回空集（存在性隐藏）
    assert await asset_projection.readable_asset_ids(session, b, ref, requested) == set()

    # A 共享给 B viewer
    await deck_service.add_share(
        session, subject=a, deck_id=deck_id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    # ③ 分享 viewer → 只签「该 deck 确实引用 ∩ 请求」的资产；越权 stranger 被丢弃（交集不变量）
    assert await asset_projection.readable_asset_ids(session, b, ref, requested) == deck_assets

    # owner 本人（≥viewer）同样只得交集，stranger 仍丢弃（不因 owner 放宽越权）
    assert await asset_projection.readable_asset_ids(session, a, ref, requested) == deck_assets

    # ④ deck 不存在 → 空集（不抛错）
    assert await asset_projection.readable_asset_ids(session, a, f'deck:{deck_id + 10_000_000}', requested) == set()


async def test_resolve_grants_private_asset_via_extra_readable(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve(extra_readable_asset_ids=...)：非 owner、无会话授权的私有资产也被签发；未列入的仍跳过。"""
    monkeypatch.setattr(
        svc_mod.StorageService,
        'signed_urls_cached',
        classmethod(lambda cls, db, **kw: _fake_sign(db, **kw)),
        raising=True,
    )
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_a_{tag}'
    viewer_b = f'h_b_{tag}'

    granted = await HasnAssetService.register_asset(
        session,
        owner_hasn_id=owner_a,
        ref=ObjectRef(
            storage_id=1, object_key=f'deck/{tag}/pic.png', access='private', stable_url='', mime='image/png', size=100
        ),
        kind='image',
    )
    other = await HasnAssetService.register_asset(
        session,
        owner_hasn_id=owner_a,
        ref=ObjectRef(
            storage_id=1,
            object_key=f'deck/{tag}/secret.png',
            access='private',
            stable_url='',
            mime='image/png',
            size=100,
        ),
        kind='image',
    )
    ids = [granted.asset_id, other.asset_id]

    # B 无任何通道（非 owner、无会话）→ 全不可读
    none_res = {r.asset_id for r in await HasnAssetService.resolve(session, requester_hasn_id=viewer_b, asset_ids=ids)}
    assert none_res == set()

    # 仅把 granted 放进 extra_readable → 只有它被签发，other 仍跳过（不越权签任意 asset）
    scoped = {
        r.asset_id
        for r in await HasnAssetService.resolve(
            session,
            requester_hasn_id=viewer_b,
            asset_ids=ids,
            extra_readable_asset_ids={granted.asset_id},
        )
    }
    assert scoped == {granted.asset_id}
