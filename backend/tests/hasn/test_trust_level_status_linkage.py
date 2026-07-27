"""doc08 RT1：信任等级 ↔ 联系人状态联动（B1 拉黑 / D1 移出黑名单恢复普通朋友）。

策略：
1. 直测纯判定函数 ``_resolve_status_on_trust_change``；
2. 以真实 PostgreSQL、真实身份和 ``SqlAlchemyRelationGateway`` 驱动端点，
   证明写路径使用 IM 角色且状态真实持久化。
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.app import contacts as contacts_api
from backend.app.hasn.api.v1.app.contacts import _resolve_status_on_trust_change
from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
from backend.app.hasn.model import HasnContacts, HasnHumans
from backend.app.hasn.schema.hasn_contacts_business import HasnTrustLevelReq
from backend.app.hasn_im.adapters.sqlalchemy_relation_gateway import (
    SqlAlchemyRelationGateway,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL


# ── 一、纯判定函数（B1 / B6 / D1） ─────────────────────────


def test_resolve_status_blocks_on_trust_zero() -> None:
    """trust=0（拉入黑名单）→ blocked，无论原状态（B1）。"""
    assert _resolve_status_on_trust_change('connected', 0) == 'blocked'
    assert _resolve_status_on_trust_change('pending', 0) == 'blocked'


def test_resolve_status_recovers_connected_from_blocked() -> None:
    """当前 blocked 且 trust≥1（移出黑名单）→ connected（D1 恢复普通朋友）。"""
    assert _resolve_status_on_trust_change('blocked', 2) == 'connected'
    assert _resolve_status_on_trust_change('blocked', 1) == 'connected'
    assert _resolve_status_on_trust_change('blocked', 5) == 'connected'


def test_resolve_status_keeps_status_on_normal_retune() -> None:
    """普通调档（非 0、原状态非 blocked）→ 保持原 status，不误改状态。"""
    assert _resolve_status_on_trust_change('connected', 3) == 'connected'
    assert _resolve_status_on_trust_change('connected', 4) == 'connected'
    assert _resolve_status_on_trust_change('pending', 2) == 'pending'


# ── 二、端点联动（真实 PostgreSQL + RelationGateway） ─────


@pytest_asyncio.fixture
async def relation_case():
    """建立隔离的真实身份与关系，测试后精确清理。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex[:18]
    owner = f'h_trust_owner_{marker}'
    peer = f'h_trust_peer_{marker}'
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过信任端点真库测试：{exc!r}')
    async with session_factory() as session:
        session.add_all(
            [
                HasnHumans(
                    hasn_id=owner,
                    star_id=f'trust-owner-{marker}',
                    user_id=int(marker[:14], 16),
                    nickname='信任测试主人',
                    status='active',
                ),
                HasnHumans(
                    hasn_id=peer,
                    star_id=f'trust-peer-{marker}',
                    user_id=int(marker[2:16], 16),
                    nickname='信任测试联系人',
                    status='active',
                ),
            ]
        )
        await session.commit()
    gateway = SqlAlchemyRelationGateway(session_factory=session_factory)
    try:
        yield session_factory, gateway, owner, peer
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(HasnContacts).where(
                    (HasnContacts.owner_id.in_([owner, peer]))
                    | (HasnContacts.peer_id.in_([owner, peer]))
                )
            )
            await session.execute(
                delete(HasnHumans).where(
                    HasnHumans.hasn_id.in_([owner, peer])
                )
            )
            await session.commit()
        await engine.dispose()


async def _invoke_real(
    relation_case,
    *,
    initial_status: str,
    initial_trust: int,
    new_trust: int,
) -> dict:
    """建立关系后通过真实端点和关系端口改档。"""
    session_factory, gateway, owner, peer = relation_case
    async with session_factory() as session:
        contact = await hasn_contacts_dao.create_contact(
            session,
            owner_id=owner,
            peer_id=peer,
            peer_type='human',
            relation_type='social',
            trust_level=initial_trust,
            status=initial_status,
            channel_source='manual',
        )
        await session.commit()
        contact_id = contact.id

    async with session_factory() as read_session:
        response = await contacts_api.update_trust_level(
            contact_id=contact_id,
            obj_in=HasnTrustLevelReq(trust_level=new_trust),
            db=read_session,
            auth={'hasn_id': owner},
            relation_gateway=gateway,
        )
    return response.data


@pytest.mark.asyncio
async def test_endpoint_blocks_contact_on_trust_zero(relation_case) -> None:
    """初始 connected，传 trust=0 → 结束时 status=blocked 且 trust_level=0（拉黑 B1）。"""
    data = await _invoke_real(
        relation_case,
        initial_status='connected',
        initial_trust=3,
        new_trust=0,
    )
    assert data['status'] == 'blocked'
    assert data['trust_level'] == 0


@pytest.mark.asyncio
async def test_endpoint_recovers_connected_from_blocked(relation_case) -> None:
    """初始 blocked，传 trust=2 → 结束时 status=connected（D1 移出黑名单恢复普通朋友）。"""
    data = await _invoke_real(
        relation_case,
        initial_status='blocked',
        initial_trust=0,
        new_trust=2,
    )
    assert data['status'] == 'connected'
    assert data['trust_level'] == 2


@pytest.mark.asyncio
async def test_endpoint_keeps_status_on_normal_retune(relation_case) -> None:
    """初始 connected，传 trust=3 → status 保持 connected（普通调档不误改状态）。"""
    data = await _invoke_real(
        relation_case,
        initial_status='connected',
        initial_trust=2,
        new_trust=3,
    )
    assert data['status'] == 'connected'
    assert data['trust_level'] == 3
