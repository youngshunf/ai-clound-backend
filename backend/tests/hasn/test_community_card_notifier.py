"""分身发完帖子/文章 → 给主人投「可点进详情」卡片消息的回归测试。"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn_community.service import community_card_notifier as notifier

_AGENT = 'a_agent01'
_OWNER = 'h_owner01'


def test_build_post_card_headline_status_and_link() -> None:
    card = notifier.build_community_resource_card(
        'post', 'p_abc123', author_name='星创', status='pending_review', preview='一段较长的正文预览……'
    )
    validate_card_message_body(card)  # 不抛即合法
    assert card['title'] == '星创发布了一篇社区帖子'
    assert card['description'] == '一段较长的正文预览……'
    # 状态 + 待审提示进 fields。
    labels = {f['label']: f['value'] for f in card['fields']}
    assert labels['状态'] == '待主人审核'
    assert '需你确认后才会公开发布' in labels['提示']
    # 资源 + 主操作深链。
    assert card['resource']['type'] == 'community.post'
    assert card['resource']['uri'] == 'hasn://community/posts/p_abc123'
    action = card['primary_action']
    assert action['kind'] == 'open_uri'
    assert action['label'] == '查看帖子'
    assert action['uri'] == 'hasn://community/posts/p_abc123'
    assert action['action_id'] == 'open_community_post'
    assert action['event']['payload'] == {'post_id': 'p_abc123'}


def test_build_article_card_headline_and_link() -> None:
    card = notifier.build_community_resource_card(
        'article', 'art_xyz789', author_name='星创', status='published', preview='深度长文标题', resource_title='深度长文标题'
    )
    validate_card_message_body(card)
    assert card['title'] == '星创发布了一篇社区文章'
    assert card['resource']['type'] == 'community.article'
    assert card['resource']['title'] == '深度长文标题'
    assert card['resource']['uri'] == 'hasn://community/articles/art_xyz789'
    labels = {f['label']: f['value'] for f in card['fields']}
    assert labels['状态'] == '已发布'
    assert '提示' not in labels  # 非待审不加审核提示
    assert card['primary_action']['label'] == '查看文章'
    assert card['primary_action']['action_id'] == 'open_community_article'


def test_build_card_author_fallback_when_name_blank() -> None:
    card = notifier.build_community_resource_card(
        'post', 'p_blank', author_name='', status='pending_review', preview=''
    )
    validate_card_message_body(card)
    assert card['title'] == '你的分身发布了一篇社区帖子'


class _FakeSessionCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_a: object) -> bool:
        return False


@pytest.fixture
def captured_route(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 async_db_session 与 deliver_system_card 换成接缝桩（非伪造结果）。"""
    captured: dict[str, Any] = {}

    async def _fake_deliver(*_args: Any, **kwargs: Any) -> int:
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(notifier, 'async_db_session', lambda: _FakeSessionCtx())
    monkeypatch.setattr(notifier, 'deliver_system_card', _fake_deliver)
    return captured


@pytest.mark.asyncio
async def test_notify_post_routes_card_to_owner(captured_route: dict[str, Any]) -> None:
    await notifier.notify_owner_post_card(
        agent_hasn_id=_AGENT,
        owner_hasn_id=_OWNER,
        author_name='星创',
        post_id='p_abc123',
        content='正文内容很长很长',
        status='pending_review',
    )
    assert captured_route['from_id'] == _AGENT
    assert captured_route['recipient_id'] == _OWNER
    assert captured_route['recipient_type'] == 'human'
    assert captured_route['peer_type'] == 'agent'
    assert captured_route['relation_type'] == 'social'
    assert captured_route['conversation_type'] == 'agent'
    assert captured_route['msg_type'] == 'message'
    assert captured_route['priority'] == 'normal'
    assert captured_route['local_id'] == 'community-card-p_abc123'
    content = captured_route['card_body']
    assert content['title'] == '星创发布了一篇社区帖子'
    assert content['resource']['uri'] == 'hasn://community/posts/p_abc123'
    assert content['primary_action']['uri'] == 'hasn://community/posts/p_abc123'


@pytest.mark.asyncio
async def test_notify_article_routes_card_to_owner(captured_route: dict[str, Any]) -> None:
    await notifier.notify_owner_article_card(
        agent_hasn_id=_AGENT,
        owner_hasn_id=_OWNER,
        author_name='星创',
        article_id='art_xyz789',
        title='深度长文',
        summary='文章摘要',
        content='文章正文',
        status='pending_review',
    )
    assert captured_route['from_id'] == _AGENT
    assert captured_route['recipient_id'] == _OWNER
    assert captured_route['peer_type'] == 'agent'
    assert captured_route['conversation_type'] == 'agent'
    assert captured_route['msg_type'] == 'message'
    content = captured_route['card_body']
    assert content['title'] == '星创发布了一篇社区文章'
    assert content['resource']['uri'] == 'hasn://community/articles/art_xyz789'
    assert content['resource']['title'] == '深度长文'


@pytest.mark.asyncio
async def test_notify_skips_when_identities_missing(captured_route: dict[str, Any]) -> None:
    await notifier.notify_owner_post_card(
        agent_hasn_id='', owner_hasn_id=_OWNER, author_name='星创', post_id='p_x', content='x', status='pending_review'
    )
    assert captured_route == {}, '缺分身/主人身份时不应触达路由'


@pytest.mark.asyncio
async def test_notify_is_best_effort_on_route_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """deliver_system_card 抛异常 → notify 必须吞掉（best-effort，发帖事务已独立提交）。"""

    async def _boom(**_kwargs: Any) -> int:
        raise RuntimeError('路由炸了')

    monkeypatch.setattr(notifier, 'async_db_session', lambda: _FakeSessionCtx())
    monkeypatch.setattr(notifier, 'deliver_system_card', _boom)

    # 不抛即通过。
    await notifier.notify_owner_post_card(
        agent_hasn_id=_AGENT, owner_hasn_id=_OWNER, author_name='星创', post_id='p_x', content='x', status='pending_review'
    )
