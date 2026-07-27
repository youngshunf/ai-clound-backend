"""RelationGateway contract suite（R2-08·真实 PG·零 mock）。

验证 `SqlAlchemyRelationGateway`（关系域对外唯一写入口）满足 `RelationGateway` port 契约、
且**行为与现网一致**（判权是搬家不是重写）：

1. 结构化子类型：adapter 实现 RelationGateway 契约（8 方法齐）；
2. `resolve_effective_relation`：读 social 直连边组 EffectiveRelation，`blocked` 按现网口径派生
   （status=='blocked' 或 trust_level==0），无边返回 None；
3. `accept_request`：human 目标互建双向边、agent 目标单向边，均回填 resulting_contact_id +
   请求转 accepted；非审批人接受被拒（authz）；
4. `reject_request`：pending → rejected，不建边；
5. `update_trust`/`block`/`unblock`：改 trust_level + status 联动（trust=0→blocked，blocked+trust≥1→connected）；
6. `remove_relation`：双向删边（隔离通知总线，仅依赖 PG）。

每用例用 uuid 派生全新 owner/peer，末尾清理自身行，不污染库。gateway 自开会话（async_db_session），
故本套件全程走真实 PG——需要本地 PG（export DATABASE_PORT=15432），不可达则跳过。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao
from backend.app.hasn.model import HasnContacts
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn.service.inbound_gatekeeper import (
    SUPPRESS,
    evaluate_inbound,
)
from backend.app.hasn_im.adapters.sqlalchemy_relation_gateway import (
    RelationGatewayError,
    SqlAlchemyRelationGateway,
)
from backend.app.hasn_im.ports.relation_gateway import RelationGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _dispose_global_engine():
    """每测试结束（其自身事件循环内）dispose 全局应用引擎池，根除跨 loop teardown 噪声。

    收编期 gateway 委托的现网 service（如 remove_contact 的 post-commit 通知推送）会在
    **全局引擎池**（pool_size=10 的 AsyncAdaptedQueuePool）上开连接。pytest-asyncio 每测试
    新建事件循环；若全局池连接跨 loop 存活，GC 时 asyncpg 会在已关闭 loop 上 cancel，抛
    「Future attached to a different loop / Event loop is closed」——被 unraisable 插件算作
    随机测试失败（本套件里随机命中 update_trust_missing / resolve_blocked_missing 等）。
    在测试自身 loop 内 dispose 即在原 loop 干净关连接，噪声根除。
    """
    yield
    from backend.database.db import async_engine

    await async_engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 RelationGateway 契约套件：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _hid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:18]}'


async def _seed_identity(
    sessionmaker,
    hasn_id: str,
    *,
    owner_id: str | None = None,
    status: str = 'active',
) -> None:
    """幂等建立真实身份行，供关系写的 fail-closed 前置使用。"""
    async with sessionmaker() as db:
        if hasn_id.startswith('h_'):
            exists = await db.scalar(
                sa.select(HasnHumans.id).where(HasnHumans.hasn_id == hasn_id)
            )
            if exists is None:
                marker = uuid.uuid4().hex
                db.add(
                    HasnHumans(
                        hasn_id=hasn_id,
                        star_id=f'h{marker[:24]}',
                        user_id=int(marker[:15], 16),
                        nickname=f'关系测试主人{marker[:10]}',
                        status=status,
                    )
                )
        elif hasn_id.startswith('a_'):
            if not owner_id:
                raise AssertionError('Agent 测试身份必须提供 owner_id')
            exists = await db.scalar(
                sa.select(HasnAgents.id).where(HasnAgents.hasn_id == hasn_id)
            )
            if exists is None:
                marker = uuid.uuid4().hex
                db.add(
                    HasnAgents(
                        hasn_id=hasn_id,
                        star_id=f'a{marker[:24]}',
                        owner_id=owner_id,
                        display_name=f'关系测试分身{marker[:8]}',
                        agent_name=f'rel{marker[:12]}',
                        api_key_hash=marker,
                        status=status,
                        created_via='client',
                    )
                )
        await db.commit()


async def _seed_contact(
    sessionmaker, *, owner_id: str, peer_id: str, peer_type: str = 'human',
    trust_level: int = 3, status: str = 'connected', peer_owner_id: str | None = None,
) -> None:
    """直接落一条 hasn_contacts 关系行（种子，精确控制 trust/status）。"""
    await _seed_identity(sessionmaker, owner_id)
    if peer_type == 'agent':
        agent_owner = peer_owner_id or _hid('h')
        await _seed_identity(sessionmaker, agent_owner)
        await _seed_identity(sessionmaker, peer_id, owner_id=agent_owner)
    else:
        await _seed_identity(sessionmaker, peer_id)
    async with sessionmaker() as db:
        db.add(HasnContacts(
            owner_id=owner_id, peer_id=peer_id, peer_type=peer_type,
            peer_owner_id=peer_owner_id, relation_type='social',
            trust_level=trust_level, status=status,
        ))
        await db.commit()


async def _seed_request(
    sessionmaker, *, from_id: str, to_id: str, to_owner_id: str,
    from_type: str = 'human', to_type: str = 'human', add_source: str = 'manual',
) -> int:
    """直接落一条 pending hasn_contact_requests，返回 request_id。"""
    if from_type == 'agent':
        await _seed_identity(sessionmaker, to_owner_id)
        await _seed_identity(sessionmaker, from_id, owner_id=to_owner_id)
    else:
        await _seed_identity(sessionmaker, from_id)
    if to_type == 'agent':
        await _seed_identity(sessionmaker, to_owner_id)
        await _seed_identity(sessionmaker, to_id, owner_id=to_owner_id)
    else:
        await _seed_identity(sessionmaker, to_id)
    async with sessionmaker() as db:
        req = await hasn_contact_requests_dao.create_request(
            db, from_id=from_id, to_id=to_id, to_owner_id=to_owner_id,
            from_type=from_type, to_type=to_type, relation_type='social',
            requested_trust_level=2, add_source=add_source,
        )
        rid = req.id
        await db.commit()
    return rid


async def _get_contact(sessionmaker, owner_id: str, peer_id: str) -> HasnContacts | None:
    async with sessionmaker() as db:
        return (await db.execute(
            sa.select(HasnContacts)
            .where(HasnContacts.owner_id == owner_id)
            .where(HasnContacts.peer_id == peer_id)
        )).scalars().first()


async def _cleanup(sessionmaker, *ids: str) -> None:
    from backend.app.hasn.model import HasnContactRequests

    async with sessionmaker() as db:
        for table, cols in (
            (HasnContacts, (HasnContacts.owner_id, HasnContacts.peer_id)),
            (HasnContactRequests, (HasnContactRequests.from_id, HasnContactRequests.to_id, HasnContactRequests.to_owner_id)),
        ):
            await db.execute(sa.delete(table).where(sa.or_(*[c.in_(ids) for c in cols])))
        await db.execute(sa.delete(HasnAgents).where(HasnAgents.hasn_id.in_(ids)))
        await db.execute(sa.delete(HasnHumans).where(HasnHumans.hasn_id.in_(ids)))
        await db.commit()


async def test_is_relation_gateway():
    """结构化子类型检查：adapter 实现 RelationGateway 契约（8 方法齐）。"""
    assert isinstance(SqlAlchemyRelationGateway(), RelationGateway)


async def test_resolve_effective_relation_connected(sessionmaker_pg):
    owner, peer = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_contact(sessionmaker_pg, owner_id=owner, peer_id=peer, trust_level=3, status='connected')
        rel = await gw.resolve_effective_relation(owner_hasn_id=owner, peer_hasn_id=peer)
        assert rel is not None
        assert rel.relation_type == 'social'
        assert rel.trust_level == 3
        assert rel.status == 'connected'
        assert rel.blocked is False
    finally:
        await _cleanup(sessionmaker_pg, owner, peer)


async def test_resolve_effective_relation_blocked_and_missing(sessionmaker_pg):
    owner, peer = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        # trust=0 → blocked 派生为 True
        await _seed_contact(sessionmaker_pg, owner_id=owner, peer_id=peer, trust_level=0, status='blocked')
        rel = await gw.resolve_effective_relation(owner_hasn_id=owner, peer_hasn_id=peer)
        assert rel is not None and rel.blocked is True
        # 无边 → None
        missing = await gw.resolve_effective_relation(owner_hasn_id=owner, peer_hasn_id=_hid('h'))
        assert missing is None
    finally:
        await _cleanup(sessionmaker_pg, owner, peer)


async def test_auto_first_contact_is_transactional_and_idempotent(
    sessionmaker_pg,
):
    """普通朋友的分身首次联系时，只建立一条待审批请求并保持消息门控。"""
    receiver_owner = _hid('h')
    receiver_agent = _hid('a')
    sender_owner = _hid('h')
    sender_agent = _hid('a')
    try:
        await _seed_identity(sessionmaker_pg, receiver_owner)
        await _seed_identity(
            sessionmaker_pg,
            receiver_agent,
            owner_id=receiver_owner,
        )
        await _seed_identity(sessionmaker_pg, sender_owner)
        await _seed_identity(
            sessionmaker_pg,
            sender_agent,
            owner_id=sender_owner,
        )
        await _seed_contact(
            sessionmaker_pg,
            owner_id=receiver_owner,
            peer_id=sender_owner,
            trust_level=2,
        )

        async with sessionmaker_pg() as db:
            first = await evaluate_inbound(
                db,
                from_id=sender_agent,
                agent_info={
                    'hasn_id': receiver_agent,
                    'owner_id': receiver_owner,
                },
            )
            replay = await evaluate_inbound(
                db,
                from_id=sender_agent,
                agent_info={
                    'hasn_id': receiver_agent,
                    'owner_id': receiver_owner,
                },
            )
            await db.commit()

        assert first.action == SUPPRESS
        assert first.reason == 'permission_denied'
        assert first.snapshot['pending_request_id'] == replay.snapshot[
            'pending_request_id'
        ]

        from backend.app.hasn.model import HasnContactRequests

        async with sessionmaker_pg() as db:
            requests = list(
                (
                    await db.execute(
                        sa.select(HasnContactRequests).where(
                            HasnContactRequests.from_id == sender_agent,
                            HasnContactRequests.to_id == receiver_agent,
                            HasnContactRequests.status == 'pending',
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(requests) == 1
        assert requests[0].to_owner_id == receiver_owner
        assert requests[0].add_source == 'auto_first_contact'
        assert await _get_contact(
            sessionmaker_pg,
            receiver_owner,
            sender_agent,
        ) is None
    finally:
        await _cleanup(
            sessionmaker_pg,
            receiver_owner,
            receiver_agent,
            sender_owner,
            sender_agent,
        )


async def test_inbound_gate_fails_closed_when_target_identity_disappears(
    sessionmaker_pg,
):
    """目标身份缺失或主人归属缺失时不得误判为放行。"""
    owner = _hid('h')
    missing_agent = _hid('a')
    sender = _hid('h')
    try:
        await _seed_identity(sessionmaker_pg, owner)
        await _seed_identity(sessionmaker_pg, sender)
        async with sessionmaker_pg() as db:
            missing_identity = await evaluate_inbound(
                db,
                from_id=sender,
                agent_info={
                    'hasn_id': missing_agent,
                    'owner_id': owner,
                },
            )
            missing_owner = await evaluate_inbound(
                db,
                from_id=sender,
                agent_info={'hasn_id': missing_agent},
            )

        assert missing_identity.action == SUPPRESS
        assert missing_identity.snapshot['decision_reason'] == 'gate_error'
        assert missing_owner.action == SUPPRESS
        assert missing_owner.snapshot['decision_reason'] == 'gate_error'
    finally:
        await _cleanup(sessionmaker_pg, owner, sender, missing_agent)


async def test_update_trust_block_unblock(sessionmaker_pg):
    owner, peer = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_contact(sessionmaker_pg, owner_id=owner, peer_id=peer, trust_level=2, status='connected')

        # update_trust → 提档，status 不变
        await gw.update_trust(owner_hasn_id=owner, peer_hasn_id=peer, trust_level=4)
        row = await _get_contact(sessionmaker_pg, owner, peer)
        assert row.trust_level == 4 and row.status == 'connected'

        # block → trust=0 + status blocked
        await gw.block(owner_hasn_id=owner, peer_hasn_id=peer)
        row = await _get_contact(sessionmaker_pg, owner, peer)
        assert row.trust_level == 0 and row.status == 'blocked'

        # unblock → blocked 翻回 connected（基线档 2）
        await gw.unblock(owner_hasn_id=owner, peer_hasn_id=peer)
        row = await _get_contact(sessionmaker_pg, owner, peer)
        assert row.trust_level == 2 and row.status == 'connected'
    finally:
        await _cleanup(sessionmaker_pg, owner, peer)


async def test_update_trust_missing_raises(sessionmaker_pg):
    owner, peer = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_identity(sessionmaker_pg, owner)
        await _seed_identity(sessionmaker_pg, peer)
        with pytest.raises(RelationGatewayError, match='关系不存在'):
            await gw.update_trust(owner_hasn_id=owner, peer_hasn_id=peer, trust_level=3)
    finally:
        await _cleanup(sessionmaker_pg, owner, peer)


async def test_inactive_identity_fails_closed(sessionmaker_pg):
    """关系存在也不能让停用身份继续修改关系状态。"""
    owner, peer = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_contact(sessionmaker_pg, owner_id=owner, peer_id=peer)
        async with sessionmaker_pg() as db:
            await db.execute(
                sa.update(HasnHumans)
                .where(HasnHumans.hasn_id == peer)
                .values(status='suspended')
            )
            await db.commit()
        with pytest.raises(RelationGatewayError, match='身份已停用'):
            await gw.update_trust(
                owner_hasn_id=owner,
                peer_hasn_id=peer,
                trust_level=3,
            )
    finally:
        await _cleanup(sessionmaker_pg, owner, peer)


async def test_accept_request_human_bidirectional(sessionmaker_pg):
    a, b = _hid('h'), _hid('h')  # a 发起、b 审批（human 目标）
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        rid = await _seed_request(sessionmaker_pg, from_id=a, to_id=b, to_owner_id=b, to_type='human')
        res = await gw.accept_request(request_id=rid, decided_by=b)
        assert res['status'] == 'connected'
        assert res['resulting_contact_id'] is not None
        # 互建双向边
        fwd = await _get_contact(sessionmaker_pg, a, b)
        rev = await _get_contact(sessionmaker_pg, b, a)
        assert fwd is not None and fwd.status == 'connected'
        assert rev is not None and rev.status == 'connected'
        # 请求转 accepted
        async with sessionmaker_pg() as db:
            req = await hasn_contact_requests_dao.get(db, rid)
        assert req.status == 'accepted' and req.resulting_contact_id == fwd.id
    finally:
        await _cleanup(sessionmaker_pg, a, b)


async def test_accept_request_agent_single_edge(sessionmaker_pg):
    a, agent, owner = _hid('h'), _hid('a'), _hid('h')  # a 发起、agent 目标、owner 审批（分身主人）
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        rid = await _seed_request(
            sessionmaker_pg, from_id=a, to_id=agent, to_owner_id=owner, to_type='agent',
        )
        res = await gw.accept_request(request_id=rid, decided_by=owner)
        assert res['status'] == 'connected'
        # 只建单向 a→agent 边，无反向
        fwd = await _get_contact(sessionmaker_pg, a, agent)
        rev = await _get_contact(sessionmaker_pg, agent, a)
        assert fwd is not None and fwd.peer_type == 'agent'
        assert rev is None
    finally:
        await _cleanup(sessionmaker_pg, a, agent, owner)


async def test_accept_request_authz_rejected(sessionmaker_pg):
    a, b, intruder = _hid('h'), _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        rid = await _seed_request(sessionmaker_pg, from_id=a, to_id=b, to_owner_id=b, to_type='human')
        # 非审批人接受 → 抛错，且不建边
        with pytest.raises(RelationGatewayError):
            await gw.accept_request(request_id=rid, decided_by=intruder)
        assert await _get_contact(sessionmaker_pg, a, b) is None
    finally:
        await _cleanup(sessionmaker_pg, a, b, intruder)


async def test_reject_request_no_edge(sessionmaker_pg):
    a, b = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        rid = await _seed_request(sessionmaker_pg, from_id=a, to_id=b, to_owner_id=b, to_type='human')
        res = await gw.reject_request(request_id=rid, decided_by=b)
        assert res['status'] == 'rejected'
        # 不建边
        assert await _get_contact(sessionmaker_pg, a, b) is None
        async with sessionmaker_pg() as db:
            req = await hasn_contact_requests_dao.get(db, rid)
        assert req.status == 'rejected'
    finally:
        await _cleanup(sessionmaker_pg, a, b)


async def test_reject_request_authz_rejected(sessionmaker_pg):
    """权限负测（R2 authz 面）：非被请求方拒绝该请求必被拒（对齐 accept 的审批人硬门）。

    reject_request 的审批人门（to_owner_id != decided_by → RelationGatewayError）与 accept 对称：
    第三方既不能替被请求方接受、也不能替其拒绝；被拒后请求仍停 pending、不落 rejected。"""
    a, b, intruder = _hid('h'), _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        rid = await _seed_request(sessionmaker_pg, from_id=a, to_id=b, to_owner_id=b, to_type='human')
        # 非被请求方拒绝 → 抛错
        with pytest.raises(RelationGatewayError):
            await gw.reject_request(request_id=rid, decided_by=intruder)
        # 请求仍停 pending（未被越权改状态），也不建边
        async with sessionmaker_pg() as db:
            req = await hasn_contact_requests_dao.get(db, rid)
        assert req.status == 'pending'
        assert await _get_contact(sessionmaker_pg, a, b) is None
    finally:
        await _cleanup(sessionmaker_pg, a, b, intruder)


async def test_remove_relation_deletes_both_edges(sessionmaker_pg):
    a, b = _hid('h'), _hid('h')
    gw = SqlAlchemyRelationGateway(session_factory=sessionmaker_pg)
    try:
        # peer_owner_id=a（=owner）使删除后的中性通知目标==owner 而被跳过 → 本用例只依赖 PG（隔离通知总线）
        await _seed_contact(sessionmaker_pg, owner_id=a, peer_id=b, peer_owner_id=a, status='connected')
        await _seed_contact(sessionmaker_pg, owner_id=b, peer_id=a, status='connected')
        res = await gw.remove_relation(owner_hasn_id=a, peer_hasn_id=b)
        assert res['deleted_edges'] == 2
        assert await _get_contact(sessionmaker_pg, a, b) is None
        assert await _get_contact(sessionmaker_pg, b, a) is None
    finally:
        await _cleanup(sessionmaker_pg, a, b)
