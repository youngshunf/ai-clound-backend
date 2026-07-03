"""联系人模块 P1: GET /api/v1/hasn/app/contacts/requests?direction=...

覆盖路由层逻辑:
- direction=received (默认) 行为不变 (回归保护)
- direction=sent 走新 DAO + target 字段
- A 发 B 收, 两边查询互不串
- direction 非法返回 422

DAO 全部 mock。HasnContacts 用 PostgreSQL JSONB 在 SQLite 上跑不起来,
因此本测试只验路由组装与字段映射。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from fastapi import HTTPException

from backend.app.hasn.api.v1.app.contacts import list_contacts, list_pending_requests

SELF = "h_aaaaaaaaaaaaaaaaaa"
PEER = "h_bbbbbbbbbbbbbbbbbb"
PEER_AGENT = "a_bbbbbbbbbbbbbbbbbb"


def _human(hasn_id: str, star_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(hasn_id=hasn_id, star_id=star_id, name=name)


def _pending_request(req_id: int, from_id: str, to_id: str, message: str = '') -> SimpleNamespace:
    """hasn_contact_requests 行（拆表后：from_id/to_id/message，目标恒 human）。"""
    return SimpleNamespace(
        id=req_id,
        from_id=from_id,
        to_id=to_id,
        to_owner_id=to_id,
        from_type='human',
        to_type='human',
        relation_type='social',
        requested_trust_level=2,
        status='pending',
        message=message,
        channel_source=None,
        add_source=None,
        created_time=None,
    )


def _connected_contact(contact_id: int, owner_id: str, peer_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=contact_id,
        owner_id=owner_id,
        peer_id=peer_id,
        peer_type='human',
        relation_type='social',
        trust_level=2,
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
        peer_owner_id=None,
    )


def _connected_agent_contact(
    contact_id: int,
    owner_id: str,
    peer_id: str,
    peer_owner_id: str,
) -> SimpleNamespace:
    contact = _connected_contact(contact_id, owner_id, peer_id)
    contact.peer_type = 'agent'
    contact.peer_owner_id = peer_owner_id
    contact.trust_level = 5
    contact.channel_source = 'system'
    return contact


class _EmptyAgentResult:
    def scalars(self) -> _EmptyAgentResult:
        return self

    def all(self) -> list:
        return []


@pytest.mark.asyncio
async def test_received_default_unchanged() -> None:
    """direction='received' 读 hasn_contact_requests.get_received_pending，契约对 daemon 不变。"""
    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_received_pending',
        new=AsyncMock(return_value=[_pending_request(101, PEER, SELF, '想加个好友')]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=_human(PEER, '100002', 'Bob')),
    ):
        resp = await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='received',
        )
    items = resp.data
    assert len(items) == 1
    assert items[0]['request_id'] == 101
    assert items[0]['from_peer']['hasn_id'] == PEER
    assert items[0]['from_peer']['name'] == 'Bob'
    # 拆表后请求恒 human→human，received 不再带 target
    assert items[0]['target'] is None
    assert items[0]['message'] == '想加个好友'


@pytest.mark.asyncio
async def test_sent_direction_returns_target() -> None:
    """direction=sent 走 get_sent_pending + target 字段（目标 human）。"""
    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_sent_pending',
        new=AsyncMock(return_value=[_pending_request(202, SELF, PEER)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=_human(PEER, '100002', 'Bob')),
    ):
        resp = await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='sent',
        )
    items = resp.data
    assert len(items) == 1
    assert items[0]['request_id'] == 202
    assert items[0]['target']['hasn_id'] == PEER
    assert items[0]['target']['name'] == 'Bob'
    # received 路径不应被触发
    assert items[0]['from_peer'] is None


@pytest.mark.asyncio
async def test_received_sender_resolution_falls_back_to_stub() -> None:
    """received: from_id 人类解析失败时回退 stub 占位（空串），不抛 500 (INV-15)。"""
    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_received_pending',
        new=AsyncMock(return_value=[_pending_request(203, 'h_unknownxxxxxxxxxx', SELF)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=None),
    ):
        resp = await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='received',
        )
    items = resp.data
    assert len(items) == 1
    assert items[0]['from_peer']['hasn_id'] == 'h_unknownxxxxxxxxxx'
    assert items[0]['from_peer']['name'] == ''
    assert items[0]['from_peer']['star_id'] == ''
    assert items[0]['target'] is None


@pytest.mark.asyncio
async def test_sent_and_received_dont_cross() -> None:
    """A 发给 B: A 看到 sent, B 看到 received, 互不串。"""
    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_sent_pending',
        new=AsyncMock(return_value=[_pending_request(303, SELF, PEER)]),
    ) as mock_sent, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_received_pending',
        new=AsyncMock(return_value=[]),
    ) as mock_received, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=_human(PEER, '100002', 'Bob')),
    ):
        resp_sent = await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='sent',
        )
        resp_received = await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='received',
        )
    assert mock_sent.await_count == 1
    assert mock_received.await_count == 1
    assert len(resp_sent.data) == 1
    assert resp_sent.data[0]['request_id'] == 303
    assert resp_received.data == []


@pytest.mark.asyncio
async def test_invalid_direction_raises_422() -> None:
    """direction 既非 received 也非 sent, 返回 422。"""
    with pytest.raises(HTTPException) as exc:
        await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='garbage',
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_sent_peer_resolution_falls_back_to_stub() -> None:
    """to_id 解析失败时返回 stub 占位, 不抛 500 (INV-15)。"""
    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_sent_pending',
        new=AsyncMock(return_value=[_pending_request(404, SELF, 'h_unknownxxxxxxxxxx')]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=None),
    ):
        resp = await list_pending_requests(
            db=AsyncMock(),
            auth={'hasn_id': SELF},
            direction='sent',
        )
    assert len(resp.data) == 1
    assert resp.data[0]['target']['hasn_id'] == 'h_unknownxxxxxxxxxx'
    # 解析失败时 stub 用空串 (schema 要求 str), 不是 None
    assert resp.data[0]['target']['name'] == ''
    assert resp.data[0]['target']['star_id'] == ''


@pytest.mark.asyncio
async def test_contact_list_returns_peer_profile_fields_from_sys_user() -> None:
    """联系人列表返回 sys_user 权威 profile 字段, 供本地 daemon 缓存。"""
    human = SimpleNamespace(
        hasn_id=PEER,
        star_id='100004',
        nickname='瘦瘦福仔',
        avatar='https://cdn.example/avatar.png',
        user_id=77,
    )
    user = SimpleNamespace(
        bio='设计师',
        gender='female',
        province='云南',
        city='昆明',
        district='五华区',
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_EmptyAgentResult())

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[_connected_contact(505, SELF, PEER)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=human),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.user_dao.get',
        new=AsyncMock(return_value=user),
    ):
        resp = await list_contacts(
            db=db,
            auth={'hasn_id': SELF},
            relation_type='social',
        )

    item = resp.data['items'][0]
    assert item['peer']['hasn_id'] == PEER
    assert item['peer']['name'] == '瘦瘦福仔'
    assert item['bio'] == '设计师'
    assert item['gender'] == 'female'
    assert item['province'] == '云南'
    assert item['city'] == '昆明'
    assert item['district'] == '五华区'


@pytest.mark.asyncio
async def test_contact_list_allows_unfiltered_full_snapshot() -> None:
    """联系人列表不传 relation_type 时返回完整联系人快照。"""
    human = SimpleNamespace(
        hasn_id=PEER,
        star_id='100004',
        nickname='瘦瘦福仔',
        avatar='https://cdn.example/avatar.png',
        user_id=77,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_EmptyAgentResult())

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[_connected_contact(505, SELF, PEER)]),
    ) as mock_list_contacts, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=human),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.user_dao.get',
        new=AsyncMock(return_value=SimpleNamespace()),
    ):
        resp = await list_contacts(
            db=db,
            auth={'hasn_id': SELF},
            relation_type=None,
        )

    item = resp.data['items'][0]
    assert mock_list_contacts.await_args.kwargs['relation_type'] is None
    assert item['peer']['hasn_id'] == PEER


@pytest.mark.asyncio
async def test_contact_list_hides_own_agent_control_relation() -> None:
    """Owner 自己的 Agent 控制边不应作为普通联系人返回。"""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_EmptyAgentResult())

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.list_contacts',
        new=AsyncMock(return_value=[_connected_agent_contact(707, SELF, PEER_AGENT, SELF)]),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_agents_dao.get_by_hasn_id',
        new=AsyncMock(
            return_value=SimpleNamespace(
                hasn_id=PEER_AGENT,
                star_id='100001#helper',
                display_name='我的助手',
                name='我的助手',
                owner_id=SELF,
            )
        ),
    ):
        resp = await list_contacts(
            db=db,
            auth={'hasn_id': SELF},
            relation_type=None,
        )

    assert resp.data['items'] == []
