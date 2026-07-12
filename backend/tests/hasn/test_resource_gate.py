"""G6 统一资源权限门（resource_gate）真实 PG 单测（doc32 §5 / doc33 S2-2）。

覆盖：rank 比较矩阵、none→404、不足→403、parent 上溯、has_own_shares 取 max、多声明、
required=False 跳过、维度②域限制三态。

零 mock：判权内核 `resolve_effective_permission` 与 hasn_humans / hasn_resource_share 查询全部打真实
PG；测试用的 adapter 是 `ResourceKindAdapter` Protocol 的**真实实现**，其「取行」后端是测试 seed 的
资源元信息 dict（平台没有通用资源表，各类型资源本就在各自应用的表里）——门的契约恰是「adapter 返回
meta 或 None」，判权链路一律真实。flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

from dataclasses import dataclass

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnHumans, HasnResourceShare
from backend.app.hasn.service.authz import resource_gate
from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn.service.authz.subject import Subject
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


# ---------- 测试用真实 adapter（后端是 seed 的资源元信息 dict） ----------


@dataclass
class _SeedRow:
    """极简 ORM-风格只读行占位（ResourceMeta.row 只需可读标量）。"""

    resource_id: str
    owner_hasn_id: str


class _KbAdapter:
    """kb 型资源 adapter（有维度②钩子，可通过 `domain_result` 配置三态）。"""

    resource_type = 'gate_test_kb'
    id_param_aliases = ('kb_id',)

    def __init__(self) -> None:
        self.store: dict[str, ResourceMeta] = {}
        # 维度②钩子返回值：None 表示不实现钩子（下方 register 时决定是否挂 agent_domain_grant）
        self.domain_result: tuple[str, list[str]] | None = None

    def put(self, resource_id: str, owner_hasn_id: str, *, visibility: str = 'private') -> None:
        self.store[resource_id] = ResourceMeta(
            resource_id=resource_id,
            owner_hasn_id=owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility=visibility,
            row=_SeedRow(resource_id, owner_hasn_id),
        )

    async def load_meta(self, db, resource_id: str) -> ResourceMeta | None:
        return self.store.get(resource_id)

    async def agent_domain_grant(self, db, owner_id: str, agent_hasn_id: str) -> tuple[str, list[str]]:
        return self.domain_result if self.domain_result is not None else ('inherit', [])


class _DocAdapter:
    """子资源 adapter：自身也有 share 行（has_own_shares），父链指向 kb。"""

    resource_type = 'gate_test_doc'
    id_param_aliases = ('doc_id',)
    has_own_shares = True

    def __init__(self, kb_of: dict[str, str]) -> None:
        self.store: dict[str, ResourceMeta] = {}
        self._kb_of = kb_of  # doc_id → kb_id

    def put(self, resource_id: str, owner_hasn_id: str, kb_id: str) -> None:
        self.store[resource_id] = ResourceMeta(
            resource_id=resource_id,
            owner_hasn_id=owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=_SeedRow(resource_id, owner_hasn_id),
        )
        self._kb_of[resource_id] = kb_id

    async def load_meta(self, db, resource_id: str) -> ResourceMeta | None:
        return self.store.get(resource_id)

    async def resolve_parent(self, db, resource_id: str) -> tuple[str, str] | None:
        kb_id = self._kb_of.get(resource_id)
        return ('gate_test_kb', kb_id) if kb_id is not None else None


class _FolderAdapter:
    """子资源 adapter：自身**无** share 行（纯父链），父链指向 kb。"""

    resource_type = 'gate_test_folder'
    id_param_aliases = ('folder_id',)

    def __init__(self, kb_of: dict[str, str]) -> None:
        self.store: dict[str, ResourceMeta] = {}
        self._kb_of = kb_of

    def put(self, resource_id: str, owner_hasn_id: str, kb_id: str) -> None:
        self.store[resource_id] = ResourceMeta(
            resource_id=resource_id,
            owner_hasn_id=owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=_SeedRow(resource_id, owner_hasn_id),
        )
        self._kb_of[resource_id] = kb_id

    async def load_meta(self, db, resource_id: str) -> ResourceMeta | None:
        return self.store.get(resource_id)

    async def resolve_parent(self, db, resource_id: str) -> tuple[str, str] | None:
        kb_id = self._kb_of.get(resource_id)
        return ('gate_test_kb', kb_id) if kb_id is not None else None


@pytest.fixture
def adapters():
    """把三个测试 adapter 注册进全局 registry，测试后精确摘除（不污染单例）。"""
    kb_of: dict[str, str] = {}
    kb = _KbAdapter()
    doc = _DocAdapter(kb_of)
    folder = _FolderAdapter(kb_of)
    for a in (kb, doc, folder):
        resource_kind_registry.register(a)
    try:
        yield kb, doc, folder
    finally:
        for rtype in ('gate_test_kb', 'gate_test_doc', 'gate_test_folder'):
            resource_kind_registry._adapters.pop(rtype, None)


async def _seed_two_humans(session) -> tuple[str, str, str, str]:
    """建资源 owner + 被分享者两个真实 human 行，返回 (h_owner, h_grantee)。"""
    tag = uuid.uuid4().hex[:8]
    from sqlalchemy import text

    rows = (
        await session.execute(
            text('SELECT id FROM sys_user WHERE id NOT IN (SELECT user_id FROM hasn_humans) ORDER BY id LIMIT 2')
        )
    ).all()
    if len(rows) < 2:
        pytest.skip('无足够空闲 sys_user 构造 human，跳过')
    u_owner, u_grantee = int(rows[0][0]), int(rows[1][0])
    h_owner, h_grantee = f'h_ro_{tag}', f'h_rg_{tag}'
    session.add_all([
        HasnHumans(hasn_id=h_owner, star_id=f'so{tag}', user_id=u_owner, nickname='owner', status='active'),
        HasnHumans(hasn_id=h_grantee, star_id=f'sg{tag}', user_id=u_grantee, nickname='grantee', status='active'),
    ])
    await session.flush()
    return h_owner, h_grantee, tag, tag


def _share(resource_type: str, resource_id: str, owner: str, grantee: str, permission: str) -> HasnResourceShare:
    return HasnResourceShare(
        resource_type=resource_type,
        resource_id=resource_id,
        owner_hasn_id=owner,
        grantee_type='human',
        grantee_id=grantee,
        permission=permission,
        granted_by=owner,
        status='active',
    )


# ---------- rank 比较矩阵 + none→404 + 不足→403 ----------


async def test_rank_matrix_and_not_found_and_forbidden(session, adapters) -> None:
    kb, _doc, _folder = adapters
    h_owner, h_grantee, _t, _t2 = await _seed_two_humans(session)
    rid = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(rid, h_owner)
    grantee = Subject.human(h_grantee)

    # 未分享 → 无权 → 404（存在性隐藏）
    with pytest.raises(errors.NotFoundError):
        await resource_gate.require(session, grantee, resource_type='gate_test_kb', resource_id=rid, need='viewer')

    # 分享 viewer：need=viewer 通过；need=editor / manager → 403
    session.add(_share('gate_test_kb', rid, h_owner, h_grantee, 'viewer'))
    await session.flush()
    got = await resource_gate.require(session, grantee, resource_type='gate_test_kb', resource_id=rid, need='viewer')
    assert got.permission == 'viewer'
    assert got.owner_hasn_id == h_owner  # owner key 来自已判权资源
    assert got.resource_id == rid
    with pytest.raises(errors.ForbiddenError):
        await resource_gate.require(session, grantee, resource_type='gate_test_kb', resource_id=rid, need='editor')
    with pytest.raises(errors.ForbiddenError):
        await resource_gate.require(session, grantee, resource_type='gate_test_kb', resource_id=rid, need='manager')


async def test_manager_share_passes_all_needs(session, adapters) -> None:
    kb, _d, _f = adapters
    h_owner, h_grantee, *_ = await _seed_two_humans(session)
    rid = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(rid, h_owner)
    session.add(_share('gate_test_kb', rid, h_owner, h_grantee, 'manager'))
    await session.flush()
    grantee = Subject.human(h_grantee)
    for need in ('viewer', 'editor', 'manager'):
        got = await resource_gate.require(session, grantee, resource_type='gate_test_kb', resource_id=rid, need=need)
        assert got.permission == 'manager'


async def test_owner_grant_short_circuits(session, adapters) -> None:
    """资源 owner 本人 → manager（owner_grant），无需 share 行。"""
    kb, _d, _f = adapters
    h_owner, _h_grantee, *_ = await _seed_two_humans(session)
    rid = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(rid, h_owner)
    await session.flush()
    got = await resource_gate.require(
        session, Subject.human(h_owner), resource_type='gate_test_kb', resource_id=rid, need='manager'
    )
    assert got.permission == 'manager'


# ---------- load_meta None（畸形/不存在）→ 404，不上溯 ----------


async def test_missing_meta_is_404(session, adapters) -> None:
    _kb, _d, _f = adapters
    with pytest.raises(errors.NotFoundError):
        await resource_gate.require(
            session, Subject.human('h_whoever'), resource_type='gate_test_kb', resource_id='not-seeded', need='viewer'
        )


async def test_unregistered_type_is_server_error(session, adapters) -> None:
    with pytest.raises(errors.ServerError):
        await resource_gate.require(
            session, Subject.human('h_x'), resource_type='no_such_type_zzz', resource_id='1', need='viewer'
        )


# ---------- parent 上溯 + has_own_shares 取 max ----------


async def test_parent_chain_folder_pure_parent(session, adapters) -> None:
    """folder 自身无 share，父库分享 viewer → folder need=viewer 通过（纯父链）。"""
    kb, _doc, folder = adapters
    h_owner, h_grantee, *_ = await _seed_two_humans(session)
    kb_id = f'kb{uuid.uuid4().hex[:6]}'
    folder_id = f'fd{uuid.uuid4().hex[:6]}'
    kb.put(kb_id, h_owner)
    folder.put(folder_id, h_owner, kb_id)
    session.add(_share('gate_test_kb', kb_id, h_owner, h_grantee, 'viewer'))
    await session.flush()
    grantee = Subject.human(h_grantee)
    got = await resource_gate.require(
        session, grantee, resource_type='gate_test_folder', resource_id=folder_id, need='viewer'
    )
    assert got.permission == 'viewer'
    # 父库仅 viewer，folder need=editor → 403
    with pytest.raises(errors.ForbiddenError):
        await resource_gate.require(
            session, grantee, resource_type='gate_test_folder', resource_id=folder_id, need='editor'
        )


async def test_doc_takes_max_of_self_and_parent(session, adapters) -> None:
    """doc 自身 share viewer + 父库 share editor → eff=max=editor → need=editor 通过。"""
    kb, doc, _folder = adapters
    h_owner, h_grantee, *_ = await _seed_two_humans(session)
    kb_id = f'kb{uuid.uuid4().hex[:6]}'
    doc_id = f'dc{uuid.uuid4().hex[:6]}'
    kb.put(kb_id, h_owner)
    doc.put(doc_id, h_owner, kb_id)
    session.add_all([
        _share('gate_test_doc', doc_id, h_owner, h_grantee, 'viewer'),  # 自身 viewer
        _share('gate_test_kb', kb_id, h_owner, h_grantee, 'editor'),  # 父库 editor
    ])
    await session.flush()
    grantee = Subject.human(h_grantee)
    got = await resource_gate.require(
        session, grantee, resource_type='gate_test_doc', resource_id=doc_id, need='editor'
    )
    assert got.permission == 'editor'


async def test_doc_own_share_only(session, adapters) -> None:
    """doc 只有自身 share（父库未分享）→ 自身档位生效（has_own_shares 分支）。"""
    kb, doc, _folder = adapters
    h_owner, h_grantee, *_ = await _seed_two_humans(session)
    kb_id = f'kb{uuid.uuid4().hex[:6]}'
    doc_id = f'dc{uuid.uuid4().hex[:6]}'
    kb.put(kb_id, h_owner)
    doc.put(doc_id, h_owner, kb_id)
    session.add(_share('gate_test_doc', doc_id, h_owner, h_grantee, 'editor'))
    await session.flush()
    grantee = Subject.human(h_grantee)
    got = await resource_gate.require(
        session, grantee, resource_type='gate_test_doc', resource_id=doc_id, need='editor'
    )
    assert got.permission == 'editor'


# ---------- enforce_declaration：多声明 + required=False 跳过 + 缺参 422 ----------


async def test_enforce_multi_declaration(session, adapters) -> None:
    kb, _doc, _folder = adapters
    h_owner, h_grantee, *_ = await _seed_two_humans(session)
    kb_a = f'kb{uuid.uuid4().hex[:6]}'
    kb_b = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(kb_a, h_owner)
    kb.put(kb_b, h_owner)
    session.add_all([
        _share('gate_test_kb', kb_a, h_owner, h_grantee, 'editor'),
        _share('gate_test_kb', kb_b, h_owner, h_grantee, 'editor'),
    ])
    await session.flush()
    grantee = Subject.human(h_grantee)
    declarations = [
        {'param': 'kb_id', 'type': 'gate_test_kb', 'need': 'editor'},
        {'param': 'target_kb_id', 'type': 'gate_test_kb', 'need': 'editor'},
    ]
    authorized = await resource_gate.enforce_declaration(
        session, grantee, declarations, {'kb_id': kb_a, 'target_kb_id': kb_b}
    )
    assert set(authorized) == {'kb_id', 'target_kb_id'}
    assert authorized['kb_id'].resource_id == kb_a
    assert authorized['target_kb_id'].resource_id == kb_b


async def test_enforce_optional_missing_skipped(session, adapters) -> None:
    kb, _doc, _folder = adapters
    h_owner, h_grantee, *_ = await _seed_two_humans(session)
    kb_id = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(kb_id, h_owner)
    session.add(_share('gate_test_kb', kb_id, h_owner, h_grantee, 'viewer'))
    await session.flush()
    grantee = Subject.human(h_grantee)
    declarations = [
        {'param': 'kb_id', 'type': 'gate_test_kb', 'need': 'viewer'},
        {'param': 'folder_id', 'type': 'gate_test_folder', 'need': 'editor', 'required': False},
    ]
    authorized = await resource_gate.enforce_declaration(session, grantee, declarations, {'kb_id': kb_id})
    assert set(authorized) == {'kb_id'}  # 可选缺省被跳过


async def test_enforce_required_missing_is_422(session, adapters) -> None:
    _kb, _doc, _folder = adapters
    declarations = [{'param': 'kb_id', 'type': 'gate_test_kb', 'need': 'viewer'}]
    with pytest.raises(errors.RequestError) as ei:
        await resource_gate.enforce_declaration(session, Subject.human('h_x'), declarations, {})
    assert ei.value.code == 422


# ---------- 维度②域限制三态（agent + owner 自有资源） ----------


async def test_dimension2_inherit_denied_restricted(session, adapters) -> None:
    kb, _doc, _folder = adapters
    h_owner, _h_grantee, *_ = await _seed_two_humans(session)
    rid = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(rid, h_owner)
    await session.flush()
    # 分身代主人（owner_grant → manager），再叠加维度②
    agent = Subject.agent(f'a_{uuid.uuid4().hex[:6]}', h_owner)

    # inherit：不裁剪 → manager
    kb.domain_result = ('inherit', [])
    got = await resource_gate.require(session, agent, resource_type='gate_test_kb', resource_id=rid, need='manager')
    assert got.permission == 'manager'

    # denied：整体禁 → 无权 404
    kb.domain_result = ('denied', [])
    with pytest.raises(errors.NotFoundError):
        await resource_gate.require(session, agent, resource_type='gate_test_kb', resource_id=rid, need='viewer')

    # restricted 且资源不在白名单 → 404
    kb.domain_result = ('restricted', ['some-other-kb'])
    with pytest.raises(errors.NotFoundError):
        await resource_gate.require(session, agent, resource_type='gate_test_kb', resource_id=rid, need='viewer')

    # restricted 且资源在白名单 → 通过
    kb.domain_result = ('restricted', [rid])
    got2 = await resource_gate.require(session, agent, resource_type='gate_test_kb', resource_id=rid, need='manager')
    assert got2.permission == 'manager'


async def test_dimension2_not_applied_to_non_owner_resource(session, adapters) -> None:
    """维度②只约束「主人自有资源」：分身访问他人资源不触发域限制（走正常 share 判权）。"""
    kb, _doc, _folder = adapters
    h_owner, h_other, *_ = await _seed_two_humans(session)
    rid = f'kb{uuid.uuid4().hex[:6]}'
    kb.put(rid, h_other)  # 资源属 h_other
    kb.domain_result = ('denied', [])  # 即便 owner 对分身设了 denied
    # 分身代 h_owner，资源属 h_other → 维度②不生效；h_owner 未获分享 → 404（非因维度②）
    agent = Subject.agent(f'a_{uuid.uuid4().hex[:6]}', h_owner)
    session.add(_share('gate_test_kb', rid, h_other, h_owner, 'editor'))  # h_other 分享给 h_owner editor
    await session.flush()
    got = await resource_gate.require(session, agent, resource_type='gate_test_kb', resource_id=rid, need='editor')
    assert got.permission == 'editor'  # 分身继承主人的 editor，维度②未误伤
