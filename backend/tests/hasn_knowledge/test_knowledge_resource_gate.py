"""G6 统一资源权限门·knowledge 接入真实 PG 守卫测试（doc33 S1-4·零 mock）。

覆盖「门这条路」——`enforce_declaration` 经 knowledge 三类 adapter（库/文档/目录）判权，把已判权
资源经 ContextVar 送达 handler。与 `test_knowledge_resource_share.py`（直测 knowledge_service ACL
单一实现）互补：本文件锁死**平台门代劳判权**的正确性——委托 owner key = 库主人 A（不是发起分身的
主人 B）、档位不足 403、撤销/不可见 404（存在性隐藏）、维度②只裁剪分身自有库不碰共享库。

不调用真实 handler（其依赖 RAGFlow 建 dataset / 索引），只驱动 `enforce_declaration` + 直插行 →
flush（不 commit）→ 断言 → rollback。共享名单复用平台 `hasn_resource_share`（knowledge_service.add_share
写入），门经 `resolve_effective_permission` 内核读之（语义不动）。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.authz import Subject
from backend.app.hasn.service.authz.resource_gate import enforce_declaration
from backend.app.hasn_knowledge.model import Document, Kb
from backend.app.hasn_knowledge.service import (
    resource_adapter as _resource_adapter,  # noqa: F401  # import 即注册 adapter
)
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.mcp.context import clear_authorized_resources, get_authorized_resource, set_authorized_resources
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

# 各工具的 resource_access 声明（与 hasn_knowledge/manifest.py 一致；此处内联以锁死门的判定契约）
_RA_KB_VIEWER = [{'param': 'kb_id', 'type': 'knowledge', 'need': 'viewer'}]
_RA_KB_EDITOR = [{'param': 'kb_id', 'type': 'knowledge', 'need': 'editor'}]
_RA_KB_MANAGER = [{'param': 'kb_id', 'type': 'knowledge', 'need': 'manager'}]
_RA_DOC_VIEWER = [{'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'viewer'}]
_RA_DOC_EDITOR = [{'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'editor'}]


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


async def _make_kb(session, owner_id: str, *, name: str = '库') -> Kb:
    kb = Kb(
        owner_id=owner_id,
        scope='personal',
        enterprise_id=None,
        name=name,
        description=None,
        ragflow_dataset_id=f'rf_{uuid.uuid4().hex[:12]}',
        embedding_model='bge',
        document_count=0,
        chunk_count=0,
        status='active',
        visibility='private',
    )
    session.add(kb)
    await session.flush()
    return kb


async def _make_doc(session, kb: Kb, *, name: str = '文档') -> Document:
    doc = Document(kb_id=kb.id, owner_id=kb.owner_id, kind='native', name=name, content='# x', parse_status='parsed')
    session.add(doc)
    await session.flush()
    return doc


async def test_gate_shared_manager_agent_edits_attributed_to_kb_owner(session) -> None:
    """场景①：A 把库共享给人 B（manager）→ B 的分身过门（kb editor / doc editor）成功，
    且委托 owner key = 库主人 A（不是发起分身的主人 B）。ContextVar 送达的正是 A。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    kb = await _make_kb(session, a.hasn_id, name='A 的报价库')
    doc = await _make_doc(session, kb, name='A 的文档')
    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='manager'
    )

    # 库级 editor：过门，档位 manager >= editor，委托键 = A
    kb_authz = await enforce_declaration(session, b_agent, _RA_KB_EDITOR, {'kb_id': kb.id})
    assert kb_authz['kb_id'].owner_hasn_id == a.hasn_id
    assert kb_authz['kb_id'].permission == 'manager'

    # 文档级 editor：父链上溯到库取并 → manager，委托键仍 = A（子资源 owner 冗余等于库 owner）
    doc_authz = await enforce_declaration(session, b_agent, _RA_DOC_EDITOR, {'doc_id': doc.id})
    assert doc_authz['doc_id'].owner_hasn_id == a.hasn_id

    # ContextVar 送达：handler 侧 get_authorized_resource 取到的 owner 就是库主人 A，落库归属正确
    try:
        set_authorized_resources(kb_authz)
        got = get_authorized_resource('kb_id')
        assert got is not None and got.owner_hasn_id == a.hasn_id
    finally:
        clear_authorized_resources()


async def test_gate_shared_viewer_reads_ok_writes_forbidden(session) -> None:
    """场景②：A 共享 viewer 给 B → B 的分身可读（viewer 过门）不可写（editor 403）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    kb = await _make_kb(session, a.hasn_id, name='A 的只读库')
    doc = await _make_doc(session, kb)
    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    # viewer 过门（读）
    ok = await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': kb.id})
    assert ok['kb_id'].owner_hasn_id == a.hasn_id and ok['kb_id'].permission == 'viewer'
    # editor 不足 → 403（有权但档位不足，非 404）
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration(session, b_agent, _RA_KB_EDITOR, {'kb_id': kb.id})
    # manager 更不足 → 403
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration(session, b_agent, _RA_KB_MANAGER, {'kb_id': kb.id})
    # 文档级：viewer 过、editor 403
    await enforce_declaration(session, b_agent, _RA_DOC_VIEWER, {'doc_id': doc.id})
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration(session, b_agent, _RA_DOC_EDITOR, {'doc_id': doc.id})


async def test_gate_revoke_and_never_shared_are_not_found(session) -> None:
    """场景③：撤销共享后 B 的分身再过门 → 404『资源不存在』（存在性隐藏，不泄露资源存在）；
    从未共享的库同样 404。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    kb = await _make_kb(session, a.hasn_id, name='A 待撤销库')
    doc = await _make_doc(session, kb)
    never = await _make_kb(session, a.hasn_id, name='A 从未共享库')

    await knowledge_service.add_share(
        session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )
    # 撤销前能读
    await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': kb.id})
    # 撤销
    await knowledge_service.revoke_share(session, subject=a, kb_id=kb.id, grantee_type='human', grantee_id=b.hasn_id)
    # 撤销后 → 404（库 + 库内文档皆不可见）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': kb.id})
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_DOC_VIEWER, {'doc_id': doc.id})
    # 从未共享库 → 404
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': never.id})
    # id 畸形 / 不存在 id → 404（不冒 500）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': 'not-an-int'})


async def test_gate_domain_restriction_only_scopes_own_kbs(session) -> None:
    """场景④：维度②（分身可动主人哪些库）只裁剪**分身自有库**，不碰 A 共享来的库。

    restricted 白名单外的自有库 → 404；denied → 自有库全 404；但 A 共享给 B 的库始终不受维度②裁剪。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    own_allowed = await _make_kb(session, b.hasn_id, name='B 自有·白名单内')
    own_blocked = await _make_kb(session, b.hasn_id, name='B 自有·白名单外')
    a_shared = await _make_kb(session, a.hasn_id, name='A 共享给 B')
    await knowledge_service.add_share(
        session, subject=a, kb_id=a_shared.id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    # 维度② restricted：白名单只含 own_allowed
    await knowledge_service.put_agent_grant(
        session, b.hasn_id, b_agent.hasn_id, mode='restricted', kb_ids=[own_allowed.id]
    )
    # 白名单内自有库：分身自有 → manager，restricted 命中白名单 → 过门
    r = await enforce_declaration(session, b_agent, _RA_KB_EDITOR, {'kb_id': own_allowed.id})
    assert r['kb_id'].owner_hasn_id == b.hasn_id
    # 白名单外自有库：restricted 未命中 → 裁剪成 none → 404
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': own_blocked.id})
    # A 共享来的库：owner=A != 分身主人 B → 维度②不触发，仍可读
    s = await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': a_shared.id})
    assert s['kb_id'].owner_hasn_id == a.hasn_id

    # 维度② denied：自有库全无权，但共享库不受影响
    await knowledge_service.put_agent_grant(session, b.hasn_id, b_agent.hasn_id, mode='denied', kb_ids=[])
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': own_allowed.id})
    s2 = await enforce_declaration(session, b_agent, _RA_KB_VIEWER, {'kb_id': a_shared.id})
    assert s2['kb_id'].owner_hasn_id == a.hasn_id


async def test_gate_optional_and_required_param_handling(session) -> None:
    """声明入参语义：可选参缺省跳过、必填参缺省 → 422（write_doc 双可选 doc_id/kb_id 场景）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    kb = await _make_kb(session, a.hasn_id, name='A 自有建文档库')

    # write_doc 式双可选声明：只传 kb_id → 只判 kb_id，doc_id 缺省跳过
    dual = [
        {'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'editor', 'required': False},
        {'param': 'kb_id', 'type': 'knowledge', 'need': 'editor', 'required': False},
    ]
    out = await enforce_declaration(session, a, dual, {'kb_id': kb.id})
    assert set(out.keys()) == {'kb_id'} and out['kb_id'].owner_hasn_id == a.hasn_id

    # 必填参缺省 → 422（RequestError），不静默直通
    with pytest.raises(errors.RequestError):
        await enforce_declaration(session, a, _RA_KB_VIEWER, {})
