"""DS-P2 设计系统云端数据层真实 PG 测试（零 mock）。

覆盖 P2 验收：建 / 查 / 出版本 / 可见域隔离 / owner 隔离 / 同步水位（owner_revision content-hash）。
直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_designsystem.model import DesignSystem
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _content(token_bg: str) -> dict:
    return {
        'tokens_css': f':root {{ --bg: {token_bg}; }}',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明',
        'components_html': '<button>Go</button>',
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'score': 88, 'grade': 'good'},
    }


async def test_save_creates_with_revision_and_bumps(session) -> None:
    """新建 → rev_no=1 + content_hash 落 + 评分存；再 save 同 id → rev_no=2 + content_hash 变。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')

    first = await design_system_service.save(
        session,
        subject=a,
        design_system_id=None,
        slug=f'sys-{tag}',
        name='暖色 SaaS',
        content=_content('#ffffff'),
        category='saas',
        score=88,
        grade='good',
        recommend_rebuild=False,
    )
    ds_id = first['id']
    assert first['revision']['rev_no'] == 1
    assert first['score'] == 88 and first['grade'] == 'good'
    assert len(first['content_hash']) == 64
    hash1 = first['content_hash']

    second = await design_system_service.save(
        session,
        subject=a,
        design_system_id=ds_id,
        slug=f'sys-{tag}',
        name='暖色 SaaS v2',
        content=_content('#f8fafc'),
        score=92,
        grade='excellent',
    )
    assert second['id'] == ds_id
    assert second['revision']['rev_no'] == 2
    assert second['content_hash'] != hash1
    assert second['name'] == '暖色 SaaS v2'

    # 版本历史降序、可取版本内容
    revs = await design_system_service.list_revisions(
        session, design_system_id=ds_id, viewer_owner_hasn_id=a.hasn_id
    )
    assert revs['total'] == 2
    assert [r['rev_no'] for r in revs['items']] == [2, 1]
    rev_full = await design_system_service.get_revision(
        session, revision_id=revs['items'][0]['id'], viewer_owner_hasn_id=a.hasn_id
    )
    assert rev_full['tokens_css'] is not None


async def test_owner_isolation_and_visibility(session) -> None:
    """A 私有设计系统：A list 可见、get 可读；B list 不可见、get → Forbidden。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')

    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'priv-{tag}', name='A 私有', content=_content('#101010')
    )
    ds_id = saved['id']

    a_list = await design_system_service.list_visible(session, viewer_owner_hasn_id=a.hasn_id)
    assert any(it['id'] == ds_id for it in a_list['items'])

    b_list = await design_system_service.list_visible(session, viewer_owner_hasn_id=b.hasn_id)
    assert all(it['id'] != ds_id for it in b_list['items'])

    with pytest.raises(errors.ForbiddenError):
        await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=b.hasn_id)


async def test_builtin_visible_cross_owner(session) -> None:
    """is_builtin=True 的官方库对任意 owner 只读可见。"""
    tag = uuid.uuid4().hex[:8]
    builtin = DesignSystem(
        owner_hasn_id='system',
        name=f'官方暖沙 {tag}',
        slug=f'seed-{tag}',
        source_kind='seed',
        is_builtin=True,
        content_hash='seedhash',
    )
    session.add(builtin)
    await session.commit()
    await session.refresh(builtin)

    stranger = Subject.human(f'h_x_{tag}')
    lst = await design_system_service.list_visible(session, viewer_owner_hasn_id=stranger.hasn_id)
    assert any(it['id'] == builtin.id and it['is_builtin'] for it in lst['items'])
    got = await design_system_service.get(session, design_system_id=builtin.id, viewer_owner_hasn_id=stranger.hasn_id)
    assert got['is_builtin'] is True


async def test_owner_revision_changes_on_save_stable_otherwise(session) -> None:
    """同步水位：save 后 owner_revision 变；不改则两次读相同（content-hash 幂等）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')

    rev_before = await design_system_service.compute_owner_revision(session, owner_hasn_id=a.hasn_id)
    await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'rv-{tag}', name='水位测试', content=_content('#222')
    )
    rev_after = await design_system_service.compute_owner_revision(session, owner_hasn_id=a.hasn_id)
    assert rev_before != rev_after

    rev_again = await design_system_service.compute_owner_revision(session, owner_hasn_id=a.hasn_id)
    assert rev_after == rev_again


async def test_delete_soft_hides(session) -> None:
    """软删 → list 不见、get → NotFound。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'del-{tag}', name='待删', content=_content('#333')
    )
    ds_id = saved['id']

    await design_system_service.delete(session, design_system_id=ds_id, owner_hasn_id=a.hasn_id)
    lst = await design_system_service.list_visible(session, viewer_owner_hasn_id=a.hasn_id)
    assert all(it['id'] != ds_id for it in lst['items'])
    with pytest.raises(errors.NotFoundError):
        await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=a.hasn_id)


async def test_collaborator_bind(session) -> None:
    """协作分身绑定（DECKBIND 对齐）：owner 绑定 → 列表可见；非 owner 不能绑。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'col-{tag}', name='协作', content=_content('#444')
    )
    ds_id = saved['id']

    await design_system_service.add_collaborator(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id, agent_hasn_id=f'a_expert_{tag}'
    )
    cols = await design_system_service.list_collaborators(
        session, design_system_id=ds_id, viewer_owner_hasn_id=a.hasn_id
    )
    assert cols['total'] == 1 and cols['items'][0]['agent_hasn_id'] == f'a_expert_{tag}'

    with pytest.raises(errors.ForbiddenError):
        await design_system_service.add_collaborator(
            session, design_system_id=ds_id, owner_hasn_id=b.hasn_id, agent_hasn_id=f'a_evil_{tag}'
        )
