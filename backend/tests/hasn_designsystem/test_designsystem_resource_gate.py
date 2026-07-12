"""G6 统一资源权限门·designsystem 接入真实 PG 守卫测试（doc33 S3-1·零 mock）。

覆盖「门这条路」——`enforce_declaration` 经 designsystem adapter 判权，把已判权资源经 ContextVar
送达 handler。与 `test_design_system_share_collab.py`（直测 design_system_service ACL 单一实现）互补：
本文件锁死**平台门代劳判权**（分身经工具面 = `mcp/tools/designsystem.py` 的 get/save 声明的
`resource_access`）的正确性——owner_grant=manager、显式 share 档位、builtin 跨 owner viewer、
撤销/未共享 404（存在性隐藏）、editor 档位不足 403、可选参缺省跳过（新建 save 路径）。

不调用真实 handler，只驱动 `enforce_declaration` + 经 service 建行/建 share → flush（不 commit）→
断言 → rollback。共享名单复用平台 `hasn_resource_share`（design_system_service.share 写入），门经
`resolve_effective_permission` 内核读之（语义不动）。builtin 行直插（service.save 不产 builtin）。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.authz.resource_gate import enforce_declaration
from backend.app.hasn_designsystem.model import DesignSystem
from backend.app.hasn_designsystem.service import (
    resource_adapter as _resource_adapter,  # noqa: F401  # import 即注册 adapter
)
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.app.mcp.context import clear_authorized_resources, get_authorized_resource, set_authorized_resources
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

# 与 mcp/tools/designsystem.py 一致的声明（内联以锁死门的判定契约）
_RA_DS_VIEWER = [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'viewer'}]
_RA_DS_EDITOR = [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'editor', 'required': False}]


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


def _content(bg: str = '#101010') -> dict:
    """一版最小内容（save 组版需要 tokens_css）。"""
    return {
        'tokens_css': f':root {{ --bg: {bg}; }}',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 说明',
        'components_html': '<button>Go</button>',
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'summary': {'score': 70, 'grade': 'fair', 'recommendRebuild': False}},
    }


async def _make_ds(session, owner: Subject, *, tag: str, enterprise_id: int | None = None) -> int:
    saved = await design_system_service.save(
        session,
        subject=owner,
        design_system_id=None,
        slug=f'ds-{tag}',
        name='设计系统',
        content=_content(),
        enterprise_id=enterprise_id,
    )
    return saved['id']


async def _make_builtin(session, *, tag: str) -> int:
    """直插一行 builtin 设计系统（service.save 不产 builtin；builtin 跨 owner 只读可见）。"""
    ds = DesignSystem(owner_hasn_id=f'h_official_{tag}', name='官方内置', slug=f'builtin-{tag}', content_hash='')
    ds.is_builtin = True
    session.add(ds)
    await session.flush()
    return ds.id


async def test_gate_owner_agent_gets_manager_attributed_to_owner(session) -> None:
    """场景①：owner A 的分身过门（owner_grant）→ manager >= viewer/editor 成功，委托 owner key = A。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    a_agent = Subject.agent(f'a_a_{tag}', a.hasn_id)
    ds_id = await _make_ds(session, a, tag=tag)

    # viewer（get）与 editor（save 更新）均过门，档位 manager，委托键 = A
    v = await enforce_declaration(session, a_agent, _RA_DS_VIEWER, {'design_system_id': ds_id})
    assert v['design_system_id'].owner_hasn_id == a.hasn_id
    assert v['design_system_id'].permission == 'manager'
    e = await enforce_declaration(session, a_agent, _RA_DS_EDITOR, {'design_system_id': ds_id})
    assert e['design_system_id'].owner_hasn_id == a.hasn_id

    # ContextVar 送达：handler 侧取到的 owner 就是 A（落库归属正确）
    try:
        set_authorized_resources(v)
        got = get_authorized_resource('design_system_id')
        assert got is not None and got.owner_hasn_id == a.hasn_id
    finally:
        clear_authorized_resources()


async def test_gate_shared_viewer_reads_ok_writes_forbidden(session) -> None:
    """场景②：A 共享 viewer 给 B → B 的分身可读（viewer 过门）不可写（editor 403）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    ds_id = await _make_ds(session, a, tag=tag)
    await design_system_service.share(
        session,
        design_system_id=ds_id,
        owner_hasn_id=a.hasn_id,
        grantee_type='human',
        grantee_id=b.hasn_id,
        permission='viewer',
    )

    ok = await enforce_declaration(session, b_agent, _RA_DS_VIEWER, {'design_system_id': ds_id})
    assert ok['design_system_id'].owner_hasn_id == a.hasn_id and ok['design_system_id'].permission == 'viewer'
    # editor 档位不足 → 403（有权但档位不足，非 404）
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration(session, b_agent, _RA_DS_EDITOR, {'design_system_id': ds_id})


async def test_gate_shared_editor_can_write(session) -> None:
    """场景③：A 共享 editor 给某分身 → 该分身 editor 过门（协作分身改 tokens）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    collab = Subject.agent(f'a_collab_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身
    ds_id = await _make_ds(session, a, tag=tag)
    await design_system_service.share(
        session,
        design_system_id=ds_id,
        owner_hasn_id=a.hasn_id,
        grantee_type='agent',
        grantee_id=collab.hasn_id,
        permission='editor',
    )

    e = await enforce_declaration(session, collab, _RA_DS_EDITOR, {'design_system_id': ds_id})
    assert e['design_system_id'].owner_hasn_id == a.hasn_id and e['design_system_id'].permission == 'editor'


async def test_gate_builtin_cross_owner_viewer_not_editor(session) -> None:
    """场景④（不动语义关键）：builtin 设计系统 → 任意非 owner 分身 viewer 可读（link）、editor 不可
    （改 builtin 被拒）。复刻 service `_readable_fast` builtin 跨 owner 只读。"""
    tag = uuid.uuid4().hex[:8]
    stranger = Subject.agent(f'a_x_{tag}', owner_hasn_id=f'h_x_{tag}')
    builtin_id = await _make_builtin(session, tag=tag)

    v = await enforce_declaration(session, stranger, _RA_DS_VIEWER, {'design_system_id': builtin_id})
    assert v['design_system_id'].permission == 'viewer'
    # editor：builtin 只读 → viewer < editor → 403（与 service「无权修改内置」同为拒）
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration(session, stranger, _RA_DS_EDITOR, {'design_system_id': builtin_id})


async def test_gate_revoke_and_never_shared_and_malformed_are_not_found(session) -> None:
    """场景⑤：撤销/从未共享私有库 → 404（存在性隐藏）；畸形/不存在 id → 404（不冒 500）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    ds_id = await _make_ds(session, a, tag=tag)
    never_id = await _make_ds(session, a, tag=f'{tag}n')

    await design_system_service.share(
        session,
        design_system_id=ds_id,
        owner_hasn_id=a.hasn_id,
        grantee_type='human',
        grantee_id=b.hasn_id,
        permission='editor',
    )
    # 撤销前能读
    await enforce_declaration(session, b_agent, _RA_DS_VIEWER, {'design_system_id': ds_id})
    await design_system_service.revoke_share(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id, grantee_type='human', grantee_id=b.hasn_id
    )
    # 撤销后 / 从未共享 → 404
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_DS_VIEWER, {'design_system_id': ds_id})
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_DS_VIEWER, {'design_system_id': never_id})
    # 畸形 id / 不存在 id → 404（不冒 500）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_DS_VIEWER, {'design_system_id': 'not-an-int'})
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _RA_DS_VIEWER, {'design_system_id': 999_000_111})


async def test_gate_optional_param_absent_skips_new_create(session) -> None:
    """声明入参语义：save 的 design_system_id 可选（required=False）——新建（缺省）→ 跳过判权，
    不炸 422（对齐 save design_system_id=None 新建路径）。"""
    tag = uuid.uuid4().hex[:8]
    a_agent = Subject.agent(f'a_a_{tag}', f'h_a_{tag}')
    out = await enforce_declaration(session, a_agent, _RA_DS_EDITOR, {})  # 无 design_system_id
    assert out == {}  # 缺省 → 无判权项
