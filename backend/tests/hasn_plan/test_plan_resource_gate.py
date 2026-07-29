"""G6 统一资源权限门·plan_event 接入真实 PG 守卫测试（doc33 S3-1·零 mock）。

覆盖「门这条路」——`enforce_declaration` 经 plan_event adapter 判权：owner 分身 owner_grant=manager、
显式 share 档位（viewer 可读不可写 / editor 可写）、撤销/未共享/畸形 id → 404（存在性隐藏）、
可选参缺省 → 跳过。与 `test_plan_enterprise_visibility_pg.py`（直测 plan_authz 区间读 WHO/WHAT）互补：
本文件锁死**平台门代劳判权**（按 id 判某个具体事件）经 plan_event adapter 的正确性，语义不动。

共享名单复用平台 `hasn_resource_share`（`ResourceShareService.upsert_share` 写入，resource_type=
`plan_event`），门经 `resolve_effective_permission` 内核读之（与 plan_authz 同口径）。事件行直插 ORM
（plan 无「建可分享事件」的专用 service 分支，`create_event` 也是普通 insert）。事务 flush 不 commit、
末尾 rollback，不污染库。需要：export DATABASE_PORT=15432。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.app.hasn.service.authz.resource_gate import enforce_declaration
from backend.app.hasn.service.authz.subject import Subject
from backend.app.hasn.service.resource_share_service import ResourceShareService
from backend.app.hasn_plan.model import Event
from backend.app.hasn_plan.service import (
    resource_adapter as _resource_adapter,  # noqa: F401  # import 即注册 plan_event adapter
)
from backend.app.hasn_plan.service.plan_authz import PLAN_EVENT_RESOURCE_TYPE
from backend.app.mcp.context import clear_authorized_resources, get_authorized_resource, set_authorized_resources
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='session')

# 与「按 id 读/写某个具体事件」等价的内联声明（内联以锁死门的判定契约，不依赖任何真实工具声明）。
_RA_EVENT_VIEWER = [{'param': 'event_id', 'type': PLAN_EVENT_RESOURCE_TYPE, 'need': 'viewer'}]
_RA_EVENT_EDITOR = [{'param': 'event_id', 'type': PLAN_EVENT_RESOURCE_TYPE, 'need': 'editor'}]
_RA_EVENT_EDITOR_OPT = [{'param': 'event_id', 'type': PLAN_EVENT_RESOURCE_TYPE, 'need': 'editor', 'required': False}]

_T0 = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


async def _make_event(
    db,
    *,
    owner_hasn_id: str,
    enterprise_id: int | None = None,
    visibility: str = 'private',
) -> int:
    """直插一行事件，返回其云端权威 id（= share 的 resource_id 口径）。"""
    ev = Event(
        owner_hasn_id=owner_hasn_id,
        enterprise_id=enterprise_id,
        title='季度评审会',
        start_at=_T0,
        end_at=_T1,
        visibility=visibility,
    )
    db.add(ev)
    await db.flush()
    return int(ev.id)


async def _share(
    db,
    *,
    event_id: int,
    owner_hasn_id: str,
    grantee_type: str,
    grantee_id: str,
    permission: str,
) -> None:
    """给某事件建一条 active 显式共享（plan_event）。"""
    await ResourceShareService.upsert_share(
        db,
        resource_type=PLAN_EVENT_RESOURCE_TYPE,
        resource_id=str(event_id),
        owner_hasn_id=owner_hasn_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        permission=permission,
        granted_by=owner_hasn_id,
    )


def _tag() -> str:
    return uuid4().hex[:8]


async def test_gate_owner_agent_gets_manager_attributed_to_owner() -> None:
    """场景①：owner A 的分身过门（owner_grant）→ manager >= viewer/editor 成功，委托 owner key = A。

    兼验 adapter 已注册且 resource_type 逐字 == plan_authz 权威常量（防两处漂移，门读的 share 才对得上）。
    """
    from backend.app.hasn.service.authz.resource_registry import resource_kind_registry

    adapter = resource_kind_registry.get(PLAN_EVENT_RESOURCE_TYPE)
    assert adapter is not None and adapter.resource_type == 'plan_event'  # import 即注册应已触发

    tag = _tag()
    async with async_db_session() as db:
        try:
            a = Subject.human(f'h_a_{tag}')
            a_agent = Subject.agent(f'a_a_{tag}', a.hasn_id)
            ev_id = await _make_event(db, owner_hasn_id=a.hasn_id)

            v = await enforce_declaration(db, a_agent, _RA_EVENT_VIEWER, {'event_id': ev_id})
            assert v['event_id'].owner_hasn_id == a.hasn_id
            assert v['event_id'].permission == 'manager'
            e = await enforce_declaration(db, a_agent, _RA_EVENT_EDITOR, {'event_id': ev_id})
            assert e['event_id'].owner_hasn_id == a.hasn_id

            # ContextVar 送达：handler 侧取到的 owner 就是 A（落库归属正确，绝不用调用者身份当资源 owner）
            try:
                set_authorized_resources(v)
                got = get_authorized_resource('event_id')
                assert got is not None and got.owner_hasn_id == a.hasn_id
            finally:
                clear_authorized_resources()
        finally:
            await db.rollback()


async def test_gate_shared_viewer_reads_ok_writes_forbidden() -> None:
    """场景②：A 共享 viewer 给 B → B 的分身可读（viewer 过门）不可写（editor 403）。"""
    tag = _tag()
    async with async_db_session() as db:
        try:
            a = Subject.human(f'h_a_{tag}')
            b = Subject.human(f'h_b_{tag}')
            b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
            ev_id = await _make_event(db, owner_hasn_id=a.hasn_id)
            await _share(
                db,
                event_id=ev_id,
                owner_hasn_id=a.hasn_id,
                grantee_type='human',
                grantee_id=b.hasn_id,
                permission='viewer',
            )

            ok = await enforce_declaration(db, b_agent, _RA_EVENT_VIEWER, {'event_id': ev_id})
            assert ok['event_id'].owner_hasn_id == a.hasn_id and ok['event_id'].permission == 'viewer'
            # editor 档位不足 → 403（有权但档位不足，非 404）
            with pytest.raises(errors.ForbiddenError):
                await enforce_declaration(db, b_agent, _RA_EVENT_EDITOR, {'event_id': ev_id})
        finally:
            await db.rollback()


async def test_gate_shared_editor_can_write() -> None:
    """场景③：A 共享 editor 给别人的分身 → 该分身 editor 过门（协作分身改事件）。"""
    tag = _tag()
    async with async_db_session() as db:
        try:
            a = Subject.human(f'h_a_{tag}')
            collab = Subject.agent(f'a_collab_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身
            ev_id = await _make_event(db, owner_hasn_id=a.hasn_id)
            await _share(
                db,
                event_id=ev_id,
                owner_hasn_id=a.hasn_id,
                grantee_type='agent',
                grantee_id=collab.hasn_id,
                permission='editor',
            )

            e = await enforce_declaration(db, collab, _RA_EVENT_EDITOR, {'event_id': ev_id})
            assert e['event_id'].owner_hasn_id == a.hasn_id and e['event_id'].permission == 'editor'
        finally:
            await db.rollback()


async def test_gate_revoke_and_never_shared_and_malformed_are_not_found() -> None:
    """场景④：撤销/从未共享私有事件 → 404（存在性隐藏）；畸形/不存在 id → 404（不冒 500）。"""
    tag = _tag()
    async with async_db_session() as db:
        try:
            a = Subject.human(f'h_a_{tag}')
            b = Subject.human(f'h_b_{tag}')
            b_agent = Subject.agent(f'a_b_{tag}', b.hasn_id)
            ev_id = await _make_event(db, owner_hasn_id=a.hasn_id)
            never_id = await _make_event(db, owner_hasn_id=a.hasn_id)
            await _share(
                db,
                event_id=ev_id,
                owner_hasn_id=a.hasn_id,
                grantee_type='human',
                grantee_id=b.hasn_id,
                permission='editor',
            )

            # 撤销前能读
            await enforce_declaration(db, b_agent, _RA_EVENT_VIEWER, {'event_id': ev_id})
            await ResourceShareService.revoke_share(
                db,
                resource_type=PLAN_EVENT_RESOURCE_TYPE,
                resource_id=str(ev_id),
                grantee_type='human',
                grantee_id=b.hasn_id,
            )
            # 撤销后 / 从未共享 → 404
            with pytest.raises(errors.NotFoundError):
                await enforce_declaration(db, b_agent, _RA_EVENT_VIEWER, {'event_id': ev_id})
            with pytest.raises(errors.NotFoundError):
                await enforce_declaration(db, b_agent, _RA_EVENT_VIEWER, {'event_id': never_id})
            # 畸形 id / 不存在 id → 404（不冒 500）
            with pytest.raises(errors.NotFoundError):
                await enforce_declaration(db, b_agent, _RA_EVENT_VIEWER, {'event_id': 'not-an-int'})
            with pytest.raises(errors.NotFoundError):
                await enforce_declaration(db, b_agent, _RA_EVENT_VIEWER, {'event_id': 999_000_111})
        finally:
            await db.rollback()


async def test_gate_public_enterprise_event_member_reads_viewer() -> None:
    """场景⑤（可见性映射·不动语义）：企业公开事件 → 该企业成员分身 viewer 可读（visibility=enterprise）。

    复刻 plan「企业公开事件对全体成员 full 可读」；同企业私有事件对非 owner 非 share 成员则 404
    （其忙闲块由区间读旁路，不经本门）。"""
    tag = _tag()
    async with async_db_session() as db:
        try:
            # 建一个真企业 + 一名 approved 成员（成员身份经 hasn_humans + hasn_enterprise_membership 解析）
            from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
            from backend.app.hasn.model.hasn_humans import HasnHumans

            base = 800_000_000 + (uuid4().int % 100_000_000)
            e_id = base + 1
            organizer = f'h_org_{tag}'
            member_hasn = f'h_mem_{tag}'
            member_uid = base + 7
            db.add(
                HasnHumans(
                    hasn_id=member_hasn,
                    user_id=member_uid,
                    star_id=str(member_uid),
                    nickname='成员',
                    status='active',
                )
            )
            db.add(HasnEnterpriseMembership(enterprise_id=e_id, user_id=member_uid, role='member', status='approved'))
            await db.flush()

            member_agent = Subject.agent(f'a_mem_{tag}', member_hasn)
            ev_public = await _make_event(db, owner_hasn_id=organizer, enterprise_id=e_id, visibility='public')
            ev_private = await _make_event(db, owner_hasn_id=organizer, enterprise_id=e_id, visibility='private')

            # 企业公开事件：成员分身 viewer 可读（经 visibility_grant）
            v = await enforce_declaration(db, member_agent, _RA_EVENT_VIEWER, {'event_id': ev_public})
            assert v['event_id'].owner_hasn_id == organizer and v['event_id'].permission == 'viewer'
            # 企业私有事件：非 owner 非 share 的成员 → none → 404（忙闲块走区间读旁路，不经本门）
            with pytest.raises(errors.NotFoundError):
                await enforce_declaration(db, member_agent, _RA_EVENT_VIEWER, {'event_id': ev_private})
        finally:
            await db.rollback()


async def test_gate_optional_param_absent_skips() -> None:
    """声明入参语义：event_id 可选（required=False）——缺省 → 跳过判权，不炸 422。"""
    tag = _tag()
    async with async_db_session() as db:
        try:
            a_agent = Subject.agent(f'a_a_{tag}', f'h_a_{tag}')
            out = await enforce_declaration(db, a_agent, _RA_EVENT_EDITOR_OPT, {})  # 无 event_id
            assert out == {}  # 缺省 → 无判权项
        finally:
            await db.rollback()
