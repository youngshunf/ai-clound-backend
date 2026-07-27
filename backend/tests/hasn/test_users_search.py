"""IM MVP S1: GET /api/v1/hasn/app/users/search

覆盖路由层逻辑：
- 唤星号精确命中（human / agent）
- 昵称前缀模糊命中
- 自己被剔除
- existing_relation 透传 status（pending|connected|archived|null）

DAO 全部 mock，因为 hasn_humans / hasn_contacts 用 PostgreSQL JSONB 在
SQLite 上无法跑；本测试只验路由组装与字段映射。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn.api.v1.app.search import search_users


def _human(hasn_id: str, star_id: str, name: str, avatar_url: str | None = None) -> SimpleNamespace:
    # 忠实真实 schema：HasnHumans 用 nickname（不是 name），_make_item 据此取显示名。
    return SimpleNamespace(
        hasn_id=hasn_id,
        star_id=star_id,
        nickname=name,
        avatar_url=avatar_url,
        status='active',
    )


def _agent(hasn_id: str, star_id: str, name: str) -> SimpleNamespace:
    # 忠实真实 schema：HasnAgents 用 display_name（不是 name）。
    return SimpleNamespace(
        hasn_id=hasn_id,
        star_id=star_id,
        display_name=name,
        avatar_url=None,
    )


def _relation(status: str) -> SimpleNamespace:
    return SimpleNamespace(status=status)


SELF = "h_selfaaaaaaaaaaaaaaaa"
TEST_DB: Any = object()


@pytest.fixture(autouse=True)
def _mock_search_by_phone():
    """auto 搜索现在也并入手机号精确命中；这些 star_id/昵称用例统一 mock 成无手机命中。

    专门的手机号命中用例在 test_contact_requests_split_integration（打真实 DB）。
    """
    with patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.search_by_phone',
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.mark.asyncio
async def test_star_id_exact_hit_human() -> None:
    """精确唤星号命中 humans，返回 type=human + 现有 connected 关系。"""
    with patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.get_by_star_id',
        new=AsyncMock(return_value=_human(
            'h_targetbbbbbbbbbbbb', '100002', 'Bob', 'http://a/b.png',
        )),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.search_by_name',
        new=AsyncMock(return_value=[]),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=_relation('connected')),
    ):
        resp = await search_users(db=TEST_DB, q='100002', limit=20, auth={'hasn_id': SELF})

    data = resp.data
    assert data['total'] == 1
    item = data['items'][0]
    assert item['hasn_id'] == 'h_targetbbbbbbbbbbbb'
    assert item['star_id'] == '100002'
    assert item['name'] == 'Bob'
    assert item['type'] == 'human'
    assert item['avatar_url'] == 'http://a/b.png'
    assert item['existing_relation'] == 'connected'


@pytest.mark.asyncio
async def test_star_id_exact_hit_agent_with_hash() -> None:
    """带 # 的唤星号查 agents 而不是 humans。"""
    human_mock = AsyncMock()  # 不应被调用（# 走 agent 分支）
    with patch(
        'backend.app.hasn.api.v1.app.search.hasn_agents_dao.get_by_star_id',
        new=AsyncMock(return_value=_agent('a_targetbbbbbbbbbbbb', 'foo#01', 'AgentZ')),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.get_by_star_id',
        new=human_mock,
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.search_by_name',
        new=AsyncMock(return_value=[]),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=None),
    ):
        resp = await search_users(db=TEST_DB, q='foo#01', limit=20, auth={'hasn_id': SELF})

    human_mock.assert_not_awaited()
    item = resp.data['items'][0]
    assert item['type'] == 'agent'
    assert item['star_id'] == 'foo#01'
    assert item['existing_relation'] is None


@pytest.mark.asyncio
async def test_name_prefix_match_excludes_self() -> None:
    """昵称前缀命中时，自己要被 DAO 剔除（exclude_hasn_id 透传）。"""
    other = _human('h_otheraaaaaaaaaaaaaa', '100099', 'Alicia')
    captured: dict = {}

    async def fake_search_by_name(db, prefix, limit, exclude_hasn_id):
        captured.update(prefix=prefix, limit=limit, exclude_hasn_id=exclude_hasn_id)
        return [other]

    with patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.get_by_star_id',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.search_by_name',
        new=AsyncMock(side_effect=fake_search_by_name),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=None),
    ):
        resp = await search_users(db=TEST_DB, q='ali', limit=20, auth={'hasn_id': SELF})

    assert captured['prefix'] == 'ali'
    assert captured['exclude_hasn_id'] == SELF
    items = resp.data['items']
    assert len(items) == 1
    assert items[0]['hasn_id'] == 'h_otheraaaaaaaaaaaaaa'


@pytest.mark.asyncio
async def test_existing_relation_status_passthrough() -> None:
    """existing_relation 必须如实透传 DB status（pending/archived 等）。"""
    target = _human('h_pendingbbbbbbbbbbb', '100003', 'Carol')

    with patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.get_by_star_id',
        new=AsyncMock(return_value=target),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.search_by_name',
        new=AsyncMock(return_value=[]),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=_relation('pending')),
    ):
        resp = await search_users(db=TEST_DB, q='100003', limit=20, auth={'hasn_id': SELF})

    assert resp.data['items'][0]['existing_relation'] == 'pending'


@pytest.mark.asyncio
async def test_exact_hit_does_not_duplicate_in_prefix_results() -> None:
    """精确命中后，前缀模糊再返回同一行不能产生重复 item。"""
    target = _human('h_dupbbbbbbbbbbbbbbbb', 'bobby', 'Bobby')

    with patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.get_by_star_id',
        new=AsyncMock(return_value=target),
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_humans_dao.search_by_name',
        new=AsyncMock(return_value=[target]),  # 故意把同一行混进前缀结果
    ), patch(
        'backend.app.hasn.api.v1.app.search.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=None),
    ):
        resp = await search_users(db=TEST_DB, q='bobby', limit=20, auth={'hasn_id': SELF})

    items = resp.data['items']
    hasn_ids = [i['hasn_id'] for i in items]
    assert hasn_ids == ['h_dupbbbbbbbbbbbbbbbb'], hasn_ids
