"""G6 统一资源权限门·design（矢量设计）接入真实 PG 守卫测试（doc33 S3-1·零 mock）。

覆盖「门这条路」——`enforce_declaration` 经 design adapter 判权，把已判权资源经 ContextVar 送达 handler。
与 `test_design_share_publish.py`（直测泛型 resource_share 对 design 生效）互补：本文件锁死**平台门代劳判权**
（design 工具面 = manifest `capabilities` 声明的 `resource_access`）的正确性——owner_grant=manager、
显式 share 档位（viewer 可读不可写 / editor 可写）、撤销/未共享/无登记串 → 404（存在性隐藏）、
editor 档位不足 → 403、可选参缺省跳过。

design 与 deck/designsystem 有一处**本质区别**：它是 daemon 本地优先权威（`design_share.py` 明载
「云端不持有项目行」，`project_id` 是 daemon 不透明字符串）。故 adapter 的 `load_meta` **从分享登记表
`hasn_resource_share` 推导资源 owner**（owner 分享项目即写一行，`owner_hasn_id`=项目主人），而非按 id 查
项目行。因此本测试用 `ResourceShareService.upsert_share` 把项目「登记进云端 ACL」（= 建 owner 事实源 + 授权），
再驱动门判权 → 断言 → fixture rollback（不 commit）。共享名单复用平台 `hasn_resource_share`，门经
`resolve_effective_permission` 内核读之（语义不动）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.authz.subject import Subject
from backend.app.hasn.service.resource_share_service import ResourceShareService
from backend.app.hasn_design.service import (
    resource_adapter as _resource_adapter,  # noqa: F401  # import 即注册 adapter（别名留空，安全）
)
from backend.app.mcp.context import clear_authorized_resources, get_authorized_resource, set_authorized_resources
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

# design adapter 的 `id_param_aliases` 已留空（`project_id` 跨应用重名，做别名会误伤 creator 等——见
# adapter 模块 docstring），故可在**模块顶层** import 触发注册而不污染 S3-2 守卫的别名集：留空别名不进
# 别名并集，`test_resource_access_declaration_contract` 不会因此把 creator 等 project_id 工具误判漏声明。

# 与 manifest `capabilities` 的 resource_access 一致的声明（内联以锁死门的判定契约）：
# 读类（get/get_selection/read_nodes/find_empty_space/export/codegen）→ viewer；写/破坏类 → editor。
_RA_DESIGN_VIEWER = [{'param': 'project_id', 'type': 'design', 'need': 'viewer'}]
_RA_DESIGN_EDITOR = [{'param': 'project_id', 'type': 'design', 'need': 'editor'}]
# 门机制测试用：可选 project_id（design 真实工具面无此形状——project_id 恒必填；仅锁死门的「可选参缺省跳过」通路）。
_RA_DESIGN_EDITOR_OPTIONAL = [{'param': 'project_id', 'type': 'design', 'need': 'editor', 'required': False}]


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


async def _share(
    session: AsyncSession,
    *,
    project_id: str,
    owner: str,
    grantee_type: str,
    grantee_id: str,
    permission: str,
) -> None:
    """经泛型 resource_share 把 design 项目登记进云端 ACL（建 owner 事实源 + 一条显式授权）。"""
    await ResourceShareService.upsert_share(
        session,
        resource_type='design',
        resource_id=project_id,
        owner_hasn_id=owner,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        permission=permission,
        granted_by=owner,
    )


def _pid(tag: str) -> str:
    """design project_id 是 daemon 不透明字符串（非整数）——用 proj_ 前缀 ULID 风格串模拟。"""
    return f'proj_{tag}'


async def test_gate_owner_agent_gets_manager_attributed_to_owner(session: AsyncSession) -> None:
    """场景①：owner A 的分身过门（owner_grant）→ manager >= viewer/editor 成功，委托 owner key = A。

    A 需已把项目分享出去（登记进云端 ACL，令 adapter 能从登记表定位 owner=A）；A 自己的分身仍 owner_grant。
    """
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    a_agent = Subject.agent(f'a_a_{tag}', a.hasn_id)
    pid = _pid(tag)
    # A 把项目分享给某人（登记 owner=A 事实源）
    await _share(
        session, project_id=pid, owner=a.hasn_id, grantee_type='human', grantee_id=f'h_reg_{tag}', permission='viewer'
    )

    # viewer 与 editor 均过门，档位 manager，委托键 = A
    v = await enforce_declaration_helper(session, a_agent, _RA_DESIGN_VIEWER, pid)
    assert v['project_id'].owner_hasn_id == a.hasn_id
    assert v['project_id'].permission == 'manager'
    e = await enforce_declaration_helper(session, a_agent, _RA_DESIGN_EDITOR, pid)
    assert e['project_id'].owner_hasn_id == a.hasn_id and e['project_id'].permission == 'manager'

    # ContextVar 送达：handler 侧取到的 owner 就是 A（本地工具 handler 委托 owner-keyed 私有方法时用它）
    try:
        set_authorized_resources(v)
        got = get_authorized_resource('project_id')
        assert got is not None and got.owner_hasn_id == a.hasn_id
    finally:
        clear_authorized_resources()


async def test_gate_shared_viewer_reads_ok_writes_forbidden(session: AsyncSession) -> None:
    """场景②：A 共享 viewer 给 B → B 的分身可读（viewer 过门）不可写（editor 403）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    pid = _pid(tag)
    await _share(
        session, project_id=pid, owner=a.hasn_id, grantee_type='human', grantee_id=b.hasn_id, permission='viewer'
    )

    ok = await enforce_declaration_helper(session, b_agent, _RA_DESIGN_VIEWER, pid)
    assert ok['project_id'].owner_hasn_id == a.hasn_id and ok['project_id'].permission == 'viewer'
    # editor 档位不足 → 403（有权但档位不足，非 404）
    with pytest.raises(errors.ForbiddenError):
        await enforce_declaration_helper(session, b_agent, _RA_DESIGN_EDITOR, pid)


async def test_gate_shared_editor_can_write(session: AsyncSession) -> None:
    """场景③：A 共享 editor 给某分身（别人的分身）→ 该分身 editor 过门（协作分身改画布）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    collab = Subject.agent(f'a_collab_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身
    pid = _pid(tag)
    await _share(
        session, project_id=pid, owner=a.hasn_id, grantee_type='agent', grantee_id=collab.hasn_id, permission='editor'
    )

    e = await enforce_declaration_helper(session, collab, _RA_DESIGN_EDITOR, pid)
    assert e['project_id'].owner_hasn_id == a.hasn_id and e['project_id'].permission == 'editor'


async def test_gate_revoke_and_never_shared_and_unregistered_are_not_found(session: AsyncSession) -> None:
    """场景④：撤销后 / 从未获授 / 无登记串 → 404（存在性隐藏）；design 无 id 解析面，故「畸形」=无登记的任意串。

    为把项目留在云端 ACL（令 adapter 仍能定位 owner=A）而单测「撤销 B 后 B 失权」，另留一条 A→C 的 active 分享。
    """
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
    d_agent = Subject.agent(f'a_d_{tag}', f'h_d_{tag}')  # 从未获授的第三方分身
    pid = _pid(tag)
    # 项目登记进云端 ACL（A→C viewer 常驻，令项目存在性可判）+ A→B editor
    await _share(
        session, project_id=pid, owner=a.hasn_id, grantee_type='human', grantee_id=f'h_c_{tag}', permission='viewer'
    )
    await _share(
        session, project_id=pid, owner=a.hasn_id, grantee_type='human', grantee_id=b.hasn_id, permission='editor'
    )

    # 撤销前 B 能读
    await enforce_declaration_helper(session, b_agent, _RA_DESIGN_VIEWER, pid)
    await ResourceShareService.revoke_share(
        session, resource_type='design', resource_id=pid, grantee_type='human', grantee_id=b.hasn_id
    )
    # 撤销后 B 失权 → 404（项目仍登记在案，存在性隐藏，非泄露 403）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration_helper(session, b_agent, _RA_DESIGN_VIEWER, pid)
    # 从未获授的第三方分身 → 404（项目登记在案但无其授权）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration_helper(session, d_agent, _RA_DESIGN_VIEWER, pid)
    # 无任何登记的任意串（= design 的「畸形/不存在」）→ load_meta None → 404（绝不冒 500）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration_helper(session, b_agent, _RA_DESIGN_VIEWER, f'unregistered-{uuid.uuid4().hex}')


async def test_gate_optional_param_absent_skips(session: AsyncSession) -> None:
    """场景⑤：声明可选（required=False）且入参缺省 → 跳过判权，不炸 422（锁死门的可选参通路）。

    design 真实工具面 project_id 恒必填（无可空新建路径），本例仅验证门的可选参机制对 design 类型成立。
    """
    tag = uuid.uuid4().hex[:8]
    a_agent = Subject.agent(f'a_a_{tag}', f'h_a_{tag}')
    out = await enforce_declaration_helper(session, a_agent, _RA_DESIGN_EDITOR_OPTIONAL, None)  # 无 project_id
    assert out == {}  # 缺省 → 无判权项


# ── 小工具：把「构造 arguments + 调门」收敛一处（arguments 只带 project_id） ─────────────────
async def enforce_declaration_helper(session, subject, declarations, project_id):
    from backend.app.hasn.service.authz.resource_gate import enforce_declaration

    arguments = {} if project_id is None else {'project_id': project_id}
    return await enforce_declaration(session, subject, declarations, arguments)
