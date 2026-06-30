"""知识库产物级协作真实 PG 测试（零 mock，复用平台 hasn_resource_share）。

knowledge 读路径仍是 owner_id keyed，本测试只覆盖叠加在其上的「权限闸 + 共享名单 + 可访问列表」
（authorize_kb/authorize_doc/authorize_folder/list_accessible_kbs/set_visibility/add_share/revoke_share）。
不调用 create_kb（其依赖真实 RAGFlow 建 dataset），改为直接插 Kb/Folder/Document 行 → flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_knowledge.model import Document, Folder, Kb
from backend.app.hasn_knowledge.service.knowledge_service import Subject, knowledge_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

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


async def _make_kb(session, owner_id: str, *, name: str = '库', visibility: str = 'private',
                   scope: str = 'personal', enterprise_id: int | None = None) -> Kb:
    kb = Kb(
        owner_id=owner_id, scope=scope, enterprise_id=enterprise_id, name=name, description=None,
        ragflow_dataset_id=f'rf_{uuid.uuid4().hex[:12]}', embedding_model='bge', document_count=0,
        chunk_count=0, status='active', visibility=visibility,
    )
    session.add(kb)
    await session.flush()
    return kb


async def test_explicit_share_human_viewer_editor(session) -> None:
    """A 私有库 → 共享给 B(editor) → B 可读不可管理 → 撤销后 B 不可见。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='报价资料')

    # B 未被共享 → 不可见（按不存在处理）
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_kb(session, subject=b, kb_id=kb.id, need='viewer')

    # A 共享 editor 给 B
    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )
    # B 可读可写
    got = await knowledge_service.authorize_kb(session, subject=b, kb_id=kb.id, need='editor')
    assert got.owner_id == a.hasn_id  # 委托键 = 库主人
    # B editor < manager → 不可管理共享/删库
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.authorize_kb(session, subject=b, kb_id=kb.id, need='manager')

    # 撤销 → B 不可见
    assert await knowledge_service.revoke_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id
    ) is True
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_kb(session, subject=b, kb_id=kb.id, need='viewer')


async def test_list_accessible_includes_shared(session) -> None:
    """list_accessible_kbs：B 的列表含「我的」(owner/manager) + 「共享给我的」(shared/viewer)。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    own = await _make_kb(session, b.hasn_id, name='B 自己的')
    shared = await _make_kb(session, a.hasn_id, name='A 共享给 B 的')
    await knowledge_service.add_share(
        session, subject=a, kb_id=shared.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    items = await knowledge_service.list_accessible_kbs(session, subject=b)
    by_id = {it['id']: it for it in items}
    assert by_id[own.id]['relation'] == 'owner' and by_id[own.id]['my_permission'] == 'manager'
    assert by_id[shared.id]['relation'] == 'shared' and by_id[shared.id]['my_permission'] == 'viewer'
    assert 'visibility' in by_id[own.id]  # 列表出参带可见性


async def test_visibility_link_grants_viewer(session) -> None:
    """可见性 link → 任意人（陌生人）拿到 viewer；private 则不可见。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    stranger = Subject.human(f'h_s_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='链接分享')

    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_kb(session, subject=stranger, kb_id=kb.id, need='viewer')

    updated = await knowledge_service.set_visibility(session, subject=a, kb_id=kb.id, visibility='link')
    assert updated['visibility'] == 'link'
    got = await knowledge_service.authorize_kb(session, subject=stranger, kb_id=kb.id, need='viewer')
    assert got.id == kb.id
    # link 只给 viewer → 陌生人不可编辑
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.authorize_kb(session, subject=stranger, kb_id=kb.id, need='editor')


async def test_doc_and_folder_gate_delegation(session) -> None:
    """authorize_doc/authorize_folder 反查所属库判权，返回库（caller 用 kb.owner_id 委托旧方法）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    c = Subject.human(f'h_c_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='含文档库')
    folder = Folder(kb_id=kb.id, owner_id=a.hasn_id, parent_id=None, name='目录', sort_order=0)
    doc = Document(kb_id=kb.id, owner_id=a.hasn_id, kind='native', name='文档', content='# x', parse_status='parsed')
    session.add_all([folder, doc])
    await session.flush()

    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )
    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=c.hasn_id, permission='viewer'
    )

    # B(editor) 过文档/目录 editor 闸，返回库主人 = A
    assert (await knowledge_service.authorize_doc(session, subject=b, doc_id=doc.id, need='editor')).owner_id == a.hasn_id
    assert (await knowledge_service.authorize_folder(session, subject=b, folder_id=folder.id, need='editor')).owner_id == a.hasn_id
    # C(viewer) 可读不可写
    await knowledge_service.authorize_doc(session, subject=c, doc_id=doc.id, need='viewer')
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.authorize_doc(session, subject=c, doc_id=doc.id, need='editor')


async def test_share_to_agent(session) -> None:
    """共享给某分身（grantee=agent）→ 该分身主体过闸。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    agent = Subject.agent(f'a_ag_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身
    kb = await _make_kb(session, a.hasn_id, name='给分身读')

    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_kb(session, subject=agent, kb_id=kb.id, need='viewer')

    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='agent', grantee_id=agent.hasn_id, permission='viewer'
    )
    got = await knowledge_service.authorize_kb(session, subject=agent, kb_id=kb.id, need='viewer')
    assert got.id == kb.id
    # 列表也含（agent 主体经 _shared_kb_ids_for_agent）
    items = await knowledge_service.list_accessible_kbs(session, subject=agent)
    assert any(it['id'] == kb.id and it['relation'] == 'shared' for it in items)


async def _make_doc(session, kb: Kb, *, name: str = '文档') -> Document:
    doc = Document(
        kb_id=kb.id, owner_id=kb.owner_id, kind='native', name=name, content='# x', parse_status='parsed'
    )
    session.add(doc)
    await session.flush()
    return doc


async def test_doc_level_share_grants_doc_only_access(session) -> None:
    """文档级共享：B 无整库访问，但凭文档 grant 能开该文档；撤销后失效。整库列表不含该库。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='私有库含敏感文档')
    doc = await _make_doc(session, kb, name='单独分享的文档')

    # B 既不可访问库，也不可访问文档
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_kb(session, subject=b, kb_id=kb.id, need='viewer')
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_doc(session, subject=b, doc_id=doc.id, need='viewer')

    # A 把单个文档共享给 B(viewer)
    out = await knowledge_service.add_doc_share(
        session, subject=a, doc_id=doc.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )
    assert out['doc_id'] == doc.id and out['kb_id'] == kb.id and out['doc_name'] == '单独分享的文档'

    # B 可开该文档（委托键 = 库主人 A），但仍不可访问整库
    got = await knowledge_service.authorize_doc(session, subject=b, doc_id=doc.id, need='viewer')
    assert got.owner_id == a.hasn_id
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_kb(session, subject=b, kb_id=kb.id, need='viewer')
    # viewer < editor → 不可写该文档
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.authorize_doc(session, subject=b, doc_id=doc.id, need='editor')
    # 整库列表不含该库（文档级共享不提升到库级可见）
    items = await knowledge_service.list_accessible_kbs(session, subject=b)
    assert all(it['id'] != kb.id for it in items)

    # 撤销 → B 不可见
    assert await knowledge_service.revoke_doc_share(
        session, subject=a, doc_id=doc.id, grantee_type='human', grantee_id=b.hasn_id
    ) is True
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.authorize_doc(session, subject=b, doc_id=doc.id, need='viewer')


async def test_doc_share_overlays_max_with_kb(session) -> None:
    """库级 viewer ∪ 文档级 editor → authorize_doc 取高者 editor 通过。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='叠加测试库')
    doc = await _make_doc(session, kb)
    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )
    await knowledge_service.add_doc_share(
        session, subject=a, doc_id=doc.id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )
    # 取高者 editor 通过；该文档可写，但整库其它文档仍只 viewer
    assert (await knowledge_service.authorize_doc(session, subject=b, doc_id=doc.id, need='editor')).owner_id == a.hasn_id
    other = await _make_doc(session, kb, name='同库另一文档')
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.authorize_doc(session, subject=b, doc_id=other.id, need='editor')


async def test_doc_share_manager_and_rejects_enterprise(session) -> None:
    """文档级 manager 可管理该文档共享名单；viewer 不可；grantee 仅 human/agent。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    c = Subject.human(f'h_c_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='文档 manager 库')
    doc = await _make_doc(session, kb)
    await knowledge_service.add_doc_share(
        session, subject=a, doc_id=doc.id, grantee_type='human', grantee_id=b.hasn_id, permission='manager'
    )
    # B(文档 manager) 可再加 C
    await knowledge_service.add_doc_share(
        session, subject=b, doc_id=doc.id, grantee_type='human', grantee_id=c.hasn_id, permission='viewer'
    )
    shares = await knowledge_service.list_doc_shares(session, subject=b, doc_id=doc.id)
    grantees = {(s['grantee_type'], s['grantee_id']) for s in shares['shares']}
    assert ('human', b.hasn_id) in grantees and ('human', c.hasn_id) in grantees
    # C(viewer) 不可管理
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.list_doc_shares(session, subject=c, doc_id=doc.id)
    # 文档级不收 enterprise grantee
    with pytest.raises(errors.RequestError):
        await knowledge_service.add_doc_share(
            session, subject=a, doc_id=doc.id, grantee_type='enterprise', grantee_id='1', permission='viewer'
        )


async def test_manager_can_manage_shares(session) -> None:
    """被授 manager 的协作者可管理共享名单；viewer 不可。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    c = Subject.human(f'h_c_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='manager 测试')
    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='manager'
    )
    # B(manager) 可再加 C
    await knowledge_service.add_share(
        session, subject=b, kb_id=kb.id, grantee_type='human', grantee_id=c.hasn_id, permission='viewer'
    )
    shares = await knowledge_service.list_shares(session, subject=b, kb_id=kb.id)
    grantees = {(s['grantee_type'], s['grantee_id']) for s in shares['shares']}
    assert ('human', b.hasn_id) in grantees
    assert ('human', c.hasn_id) in grantees
    # C(viewer) 不可管理
    with pytest.raises(errors.ForbiddenError):
        await knowledge_service.list_shares(session, subject=c, kb_id=kb.id)
