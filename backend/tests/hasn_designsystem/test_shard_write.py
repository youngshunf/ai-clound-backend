"""设计系统「分片写入」service 测试（DSPUT·真实 PG，禁 mock）。

覆盖 create_shell → put_content / put_gallery_scene → finalize 这条链的核心承诺：
- 未提供的字段**从当前版继承**（整包 save 里「没传」等于「清空」，这里等于「不动」）；
- 派生物（design-tokens.json / tailwind / 契约报告 / 组件清单）由**服务端现算**，分身不必回传；
- 按场景写画廊时其余场景**逐字节不变**；
- finalize 内容未变时不落冗余版本，但完成判定照做；complete 取「五项必填是否真写全」。

需活体 DB（本地 15432）：DATABASE_PORT=15432 pytest backend/tests/hasn_designsystem/test_shard_write.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn_designsystem.core import SourceToken, compile_tokens
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service

_GENERATED_AT = '2026-08-25T00:00:00+00:00'

_BRAND_SECTION = (
    '<section data-ds-scene="brand_website">'
    '<nav data-ds-component="nav" style="color:var(--fg)">导航</nav>'
    '<div data-ds-component="hero" style="background:var(--bg)">Hero</div>'
    '<div data-ds-component="features" style="color:var(--fg-2)">特性</div>'
    '<button data-ds-component="cta" style="background:var(--accent)">CTA</button>'
    '<footer data-ds-component="footer" style="color:var(--muted)">页脚</footer>'
    '</section>'
)
_DECK_SECTION = (
    '<section data-ds-scene="deck">'
    '<div data-ds-component="cover" style="background:var(--bg)">封面</div>'
    '<div data-ds-component="section" style="color:var(--fg)">章节</div>'
    '<div data-ds-component="bullets" style="color:var(--fg-2)">要点</div>'
    '<div data-ds-component="chart" style="border:1px solid var(--border)">图表</div>'
    '<div data-ds-component="closing" style="background:var(--accent)">结束</div>'
    '</section>'
)


def _tokens_css() -> str:
    """用 compile_tokens 造一份合规 tokens.css（缺槽由契约引擎回填，保证 56 槽齐）。"""
    source = [
        SourceToken(name='--bg', value='#ffffff', source='test', line=None, usage=[]),
        SourceToken(name='--fg', value='#0f172a', source='test', line=None, usage=[]),
        SourceToken(name='--accent', value='#2563eb', source='test', line=None, usage=[]),
    ]
    return compile_tokens(source, _GENERATED_AT).tokens_css


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


async def _cleanup(design_system_id: int | None) -> None:
    if design_system_id is None:
        return
    from sqlalchemy import delete

    from backend.app.hasn_designsystem.model.design_system import DesignSystem
    from backend.app.hasn_designsystem.model.revision import Revision
    from backend.database.db import async_db_session

    async with async_db_session.begin() as db:
        await db.execute(delete(Revision).where(Revision.design_system_id == design_system_id))
        await db.execute(delete(DesignSystem).where(DesignSystem.id == design_system_id))


def _subject() -> tuple[Subject, str]:
    """用 human 主体：本文件验的是内容继承/派生/场景隔离/完整度，不验完成卡投递。

    分身主体会在内容写全那一刻触发真实 IM 投递（测试库里没有该分身身份 → ImSendRejected 回滚整次写），
    那条链路属于发卡，另有其测试；而 ``finalize`` 的 ``complete`` 取「五项必填是否真写全」、
    **不取发卡水位**，正是为了让 owner 本人路径也能得到诚实的完整度——用 human 恰好一并验到这点。
    """
    owner = f'h_ds_shard_{uuid.uuid4().hex[:16]}'
    return Subject.human(owner), owner


async def _current_revision(design_system_id: int) -> dict:
    from backend.app.hasn_designsystem.model.design_system import DesignSystem
    from backend.app.hasn_designsystem.model.revision import Revision
    from backend.database.db import async_db_session

    async with async_db_session() as db:
        d = await db.get(DesignSystem, design_system_id)
        assert d is not None
        if d.current_revision_id is None:
            return {}
        rev = await db.get(Revision, d.current_revision_id)
        assert rev is not None
        return {
            'rev_no': rev.rev_no,
            'tokens_css': rev.tokens_css,
            'design_md': rev.design_md,
            'components_html': rev.components_html,
            'components_manifest_json': rev.components_manifest_json,
            'token_contract_report_json': rev.token_contract_report_json,
            'design_tokens_json': rev.design_tokens_json,
            'tailwind_css': rev.tailwind_css,
        }


@pytest.mark.asyncio(loop_scope='session')
async def test_create_shell_is_idempotent_and_has_no_revision() -> None:
    """建壳只要 slug+name，不落 revision；同 slug 重复建壳幂等命中，不造第二套也不覆盖内容。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.database.db import async_db_session

    subject, _ = _subject()
    slug = f'ds-shell-{uuid.uuid4().hex[:8]}'
    ds_id = None
    try:
        async with async_db_session() as db:
            first = await design_system_service.create_shell(db, subject=subject, slug=slug, name='壳一号')
        ds_id = first['id']
        assert first['created'] is True
        assert first['slug'] == slug
        assert await _current_revision(ds_id) == {}  # 空壳没有内容 → 没有版本

        async with async_db_session() as db:
            again = await design_system_service.create_shell(db, subject=subject, slug=slug, name='壳一号（重试）')
        assert again['id'] == ds_id
        assert again['created'] is False  # 幂等命中如实告知，不假装新建
        assert again['name'] == '壳一号'  # 不覆盖既有展示名
    finally:
        await _cleanup(ds_id)


@pytest.mark.asyncio(loop_scope='session')
async def test_put_content_inherits_unprovided_fields() -> None:
    """分片写的核心承诺：只传 design_md，tokens_css 与画廊必须原样还在。

    整包 save 在同样的入参下会把它们清空——这正是「漏传 components_html 导致画廊 13/13 变 0/13、
    而 save 照样返回 200」那个事故的形状。
    """
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.database.db import async_db_session

    subject, _ = _subject()
    ds_id = None
    try:
        async with async_db_session() as db:
            shell = await design_system_service.create_shell(
                db, subject=subject, slug=f'ds-inherit-{uuid.uuid4().hex[:8]}', name='继承测试'
            )
        ds_id = shell['id']

        async with async_db_session() as db:
            await design_system_service.put_content(
                db, subject=subject, design_system_id=ds_id, patch={'tokens_css': _tokens_css()}
            )
        async with async_db_session() as db:
            await design_system_service.put_gallery_scene(
                db, subject=subject, design_system_id=ds_id, scene='brand_website', html=_BRAND_SECTION
            )
        async with async_db_session() as db:
            await design_system_service.put_content(
                db, subject=subject, design_system_id=ds_id, patch={'design_md': '# 设计说明\n只改这一块。'}
            )

        rev = await _current_revision(ds_id)
        assert rev['design_md'].startswith('# 设计说明')
        assert '--accent' in rev['tokens_css']  # 没传 → 从当前版继承，不是清空
        assert 'data-ds-component="hero"' in rev['components_html']
    finally:
        await _cleanup(ds_id)


@pytest.mark.asyncio(loop_scope='session')
async def test_server_recomputes_derived_artifacts() -> None:
    """分身只传 tokens.css 与画廊，四项派生物由服务端现算——它们本就是纯函数的输出。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.database.db import async_db_session

    subject, _ = _subject()
    ds_id = None
    try:
        async with async_db_session() as db:
            shell = await design_system_service.create_shell(
                db, subject=subject, slug=f'ds-derive-{uuid.uuid4().hex[:8]}', name='派生测试'
            )
        ds_id = shell['id']
        async with async_db_session() as db:
            out = await design_system_service.put_content(
                db, subject=subject, design_system_id=ds_id, patch={'tokens_css': _tokens_css()}
            )
        assert out['score'] == 100  # 评分来自服务端现算的契约报告，不是分身报的
        assert out['grade'] == 'excellent'

        rev = await _current_revision(ds_id)
        assert rev['token_contract_report_json']['summary']['score'] == 100
        assert rev['token_contract_report_json']['selfCheck']['ok'] is True
        # design-tokens.json 必须落成 JSONB 对象，不是被当字符串标量塞进去
        assert isinstance(rev['design_tokens_json'], dict)
        assert rev['design_tokens_json']['format'] == 'hasn-design-tokens/v1'
        assert '@theme' in rev['tailwind_css']

        async with async_db_session() as db:
            await design_system_service.put_gallery_scene(
                db, subject=subject, design_system_id=ds_id, scene='deck', html=_DECK_SECTION
            )
        rev = await _current_revision(ds_id)
        assert isinstance(rev['components_manifest_json'], dict)
        scene_ids = {s['id'] for s in rev['components_manifest_json'].get('scenes', [])}
        assert 'deck' in scene_ids  # 组件清单也是服务端从 HTML 现抽的
    finally:
        await _cleanup(ds_id)


@pytest.mark.asyncio(loop_scope='session')
async def test_put_gallery_scene_leaves_other_scenes_intact() -> None:
    """按场景写画廊：写 deck 时 brand_website 那一块逐字节不变。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.app.hasn_designsystem.core.gallery_compose import split_gallery
    from backend.database.db import async_db_session

    subject, _ = _subject()
    ds_id = None
    try:
        async with async_db_session() as db:
            shell = await design_system_service.create_shell(
                db, subject=subject, slug=f'ds-scene-{uuid.uuid4().hex[:8]}', name='场景测试'
            )
        ds_id = shell['id']
        async with async_db_session() as db:
            await design_system_service.put_content(
                db, subject=subject, design_system_id=ds_id, patch={'tokens_css': _tokens_css()}
            )
        async with async_db_session() as db:
            await design_system_service.put_gallery_scene(
                db, subject=subject, design_system_id=ds_id, scene='brand_website', html=_BRAND_SECTION
            )
        brand_before = split_gallery((await _current_revision(ds_id))['components_html']).scenes['brand_website']

        async with async_db_session() as db:
            out = await design_system_service.put_gallery_scene(
                db, subject=subject, design_system_id=ds_id, scene='deck', html=_DECK_SECTION
            )
        assert out['scene'] == 'deck'
        parts = split_gallery((await _current_revision(ds_id))['components_html'])
        assert parts.scenes['brand_website'] == brand_before
        assert '封面' in parts.scenes['deck']
    finally:
        await _cleanup(ds_id)


@pytest.mark.asyncio(loop_scope='session')
async def test_put_gallery_scene_rejects_scene_mismatch() -> None:
    """markup 里是 brand_website 却声明写 deck → 400 如实拒绝，不静默把内容盖到 deck 名下。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.common.exception import errors
    from backend.database.db import async_db_session

    subject, _ = _subject()
    ds_id = None
    try:
        async with async_db_session() as db:
            shell = await design_system_service.create_shell(
                db, subject=subject, slug=f'ds-mismatch-{uuid.uuid4().hex[:8]}', name='错场景测试'
            )
        ds_id = shell['id']
        with pytest.raises(errors.RequestError, match='brand_website'):
            async with async_db_session() as db:
                await design_system_service.put_gallery_scene(
                    db, subject=subject, design_system_id=ds_id, scene='deck', html=_BRAND_SECTION
                )
    finally:
        await _cleanup(ds_id)


@pytest.mark.asyncio(loop_scope='session')
async def test_put_content_rejects_unknown_field() -> None:
    """patch 里出现不认识的内容字段 → 明确报错并列出可写字段，不静默丢弃。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.common.exception import errors
    from backend.database.db import async_db_session

    subject, _ = _subject()
    ds_id = None
    try:
        async with async_db_session() as db:
            shell = await design_system_service.create_shell(
                db, subject=subject, slug=f'ds-unknown-{uuid.uuid4().hex[:8]}', name='未知字段测试'
            )
        ds_id = shell['id']
        with pytest.raises(errors.RequestError, match='design_markdown'):
            async with async_db_session() as db:
                await design_system_service.put_content(
                    db, subject=subject, design_system_id=ds_id, patch={'design_markdown': '写错字段名了'}
                )
    finally:
        await _cleanup(ds_id)


@pytest.mark.asyncio(loop_scope='session')
async def test_finalize_reports_completeness_and_skips_redundant_revision() -> None:
    """finalize：内容没变不落冗余版本，但完整度判定照做，并回场景自查报告。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    from backend.database.db import async_db_session

    subject, _ = _subject()
    ds_id = None
    try:
        async with async_db_session() as db:
            shell = await design_system_service.create_shell(
                db, subject=subject, slug=f'ds-final-{uuid.uuid4().hex[:8]}', name='定稿测试'
            )
        ds_id = shell['id']
        async with async_db_session() as db:
            await design_system_service.put_content(
                db, subject=subject, design_system_id=ds_id, patch={'tokens_css': _tokens_css()}
            )
        # 只写了 tokens → 五项必填没齐，此时定稿必须如实说「不完整」
        async with async_db_session() as db:
            partial = await design_system_service.finalize(db, subject=subject, design_system_id=ds_id)
        assert partial['complete'] is False
        assert partial['scene_report']['complete'] is False

        async with async_db_session() as db:
            await design_system_service.put_content(
                db, subject=subject, design_system_id=ds_id, patch={'design_md': '# 说明'}
            )
        async with async_db_session() as db:
            await design_system_service.put_gallery_scene(
                db, subject=subject, design_system_id=ds_id, scene='brand_website', html=_BRAND_SECTION
            )
        rev_before = (await _current_revision(ds_id))['rev_no']

        async with async_db_session() as db:
            done = await design_system_service.finalize(db, subject=subject, design_system_id=ds_id)
        assert done['complete'] is True  # 五项必填齐了
        assert done['unchanged'] is True  # 定稿本身没改内容 → 不落冗余版本
        assert done['scene_report']['complete'] is True  # 品牌网站 5 件齐
        assert (await _current_revision(ds_id))['rev_no'] == rev_before
    finally:
        await _cleanup(ds_id)
