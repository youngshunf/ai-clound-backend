"""联系人列表：peer 本身是分身时必须带实时在线态。

2026-08-23 线上故障回归。主人把好友的分身**直接加为联系人**后，那一行的 peer 在
`GET /api/v1/hasn/app/contacts` 里恒无 `online_status`：

1. `HasnContactPeerOut` 没声明这一格 —— 就算 service 层算出来，Pydantic 也会静默丢掉；
2. `list_contacts` 手搓 peer 时压根没传。

两处叠加的结果是：分身在 Redis presence 里 ready=1（真在线、能收消息），对方客户端却
恒显示离线，并被 WebUI 的「在线态硬门控」禁掉输入框，提示「分身当前不在线，无法接收消息」。

DAO 全部 mock（HasnContacts 用 PostgreSQL JSONB，SQLite 跑不起来），本测试只验路由组装
与字段映射——而这正好是当时断掉的那一层。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn.api.v1.app.contacts import list_contacts
from backend.app.hasn.schema.hasn_contacts_business import HasnContactPeerOut

SELF = 'h_aaaaaaaaaaaaaaaaaa'
PEER_HUMAN = 'h_junzi00000000000000'
PEER_AGENT = 'a_zhiwei00000000000000'


def _contact_row(contact_id: int, peer_id: str, peer_type: str, peer_owner_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=contact_id,
        owner_id=SELF,
        peer_id=peer_id,
        peer_type=peer_type,
        peer_owner_id=peer_owner_id,
        relation_type='social',
        trust_level=4,
        nickname=None,
        tags=None,
        subscription=False,
        status='connected',
        channel_source=None,
        add_source=None,
        custom_permissions=None,
        scope=None,
        connected_at=None,
        last_interaction_at=None,
        interaction_count=0,
        request_message=None,
        auto_expire=None,
    )


def _owner_human() -> SimpleNamespace:
    return SimpleNamespace(hasn_id=PEER_HUMAN, star_id='100002', nickname='菌子', avatar=None, user_id=None)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        hasn_id=PEER_AGENT,
        star_id='100002#assistant-2',
        display_name='知微',
        owner_id=PEER_HUMAN,
        avatar=None,
    )


async def _humans_lookup(_db, hasn_id: str):
    """peer 是分身 → 第一次查 human 落空；随后按 owner_id 查主人摘要要命中。"""
    return _owner_human() if hasn_id == PEER_HUMAN else None


def test_peer_schema_declares_online_status() -> None:
    """schema 必须有这一格，否则 service 算出来也会被 Pydantic 丢掉（断点 1）。"""
    peer = HasnContactPeerOut(
        hasn_id=PEER_AGENT,
        star_id='100002#assistant-2',
        name='知微',
        type='agent',
        online_status='online',
    )
    assert peer.model_dump()['online_status'] == 'online'


@pytest.mark.asyncio
async def test_agent_peer_carries_realtime_online_status() -> None:
    """peer 是分身 → 列表必须回填 Redis presence 判定的实时在线态（断点 2）。"""
    db = AsyncMock()

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[_contact_row(601, PEER_AGENT, 'agent', PEER_HUMAN)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(side_effect=_humans_lookup),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_agents_dao.get_by_hasn_id',
        new=AsyncMock(return_value=_agent()),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts._presence_query.get_online_map',
        new=AsyncMock(return_value={PEER_AGENT: True}),
    ):
        resp = await list_contacts(db=db, identity_db=db, auth={'hasn_id': SELF}, relation_type=None)

    peer = resp.data['items'][0]['peer']
    assert peer['hasn_id'] == PEER_AGENT
    assert peer['online_status'] == 'online'


@pytest.mark.asyncio
async def test_agent_peer_reports_offline_when_presence_says_so() -> None:
    """presence 判离线时如实报离线——不能因为「有这行联系人」就当在线。"""
    db = AsyncMock()

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[_contact_row(602, PEER_AGENT, 'agent', PEER_HUMAN)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(side_effect=_humans_lookup),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_agents_dao.get_by_hasn_id',
        new=AsyncMock(return_value=_agent()),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts._presence_query.get_online_map',
        new=AsyncMock(return_value={PEER_AGENT: False}),
    ):
        resp = await list_contacts(db=db, identity_db=db, auth={'hasn_id': SELF}, relation_type=None)

    assert resp.data['items'][0]['peer']['online_status'] == 'offline'


@pytest.mark.asyncio
async def test_human_peer_leaves_online_status_null_and_skips_presence_lookup() -> None:
    """人的 peer 没有「分身在线态」这个概念：保持 None，且不为此打 Redis。"""
    db = AsyncMock()
    presence = AsyncMock(return_value={})

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[_contact_row(603, PEER_HUMAN, 'human', None)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=_owner_human()),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.HasnContactsService.fetch_owned_agents_with_status',
        new=AsyncMock(return_value=[]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts._presence_query.get_online_map',
        new=presence,
    ):
        resp = await list_contacts(db=db, identity_db=db, auth={'hasn_id': SELF}, relation_type=None)

    assert resp.data['items'][0]['peer']['online_status'] is None
    presence.assert_not_awaited()


@pytest.mark.asyncio
async def test_presence_lookup_is_batched_once_for_all_agent_peers() -> None:
    """多行分身联系人只打一次 Redis——避免在循环里逐行查（N+1）。"""
    other_agent = 'a_niulvshi000000000000'
    db = AsyncMock()
    presence = AsyncMock(return_value={PEER_AGENT: True, other_agent: False})

    def _agent_lookup(_db, hasn_id: str):
        agent = _agent()
        if hasn_id == other_agent:
            agent = SimpleNamespace(
                hasn_id=other_agent,
                star_id='100002#legal-document',
                display_name='牛律师',
                owner_id=PEER_HUMAN,
                avatar=None,
            )
        return agent

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[
            _contact_row(604, PEER_AGENT, 'agent', PEER_HUMAN),
            _contact_row(605, other_agent, 'agent', PEER_HUMAN),
        ]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(side_effect=_humans_lookup),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_agents_dao.get_by_hasn_id',
        new=AsyncMock(side_effect=_agent_lookup),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts._presence_query.get_online_map',
        new=presence,
    ):
        resp = await list_contacts(db=db, identity_db=db, auth={'hasn_id': SELF}, relation_type=None)

    presence.assert_awaited_once()
    assert sorted(presence.await_args.args[0]) == sorted([PEER_AGENT, other_agent])
    statuses = {item['peer']['hasn_id']: item['peer']['online_status'] for item in resp.data['items']}
    assert statuses == {PEER_AGENT: 'online', other_agent: 'offline'}
