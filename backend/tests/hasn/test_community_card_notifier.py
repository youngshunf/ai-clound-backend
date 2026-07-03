"""分身发完帖子/文章 → 给主人投「可点进详情」卡片消息的回归测试。

覆盖（不依赖 PG，纯逻辑 + 路由接缝；route_message 真实行为由 message_router 自有测试覆盖）：
1. 卡片体构造——标题「{分身名}发布了一篇社区{帖子|文章}」、携状态+审核提示、过 schema 自检、
   URI/资源类型/primary_action 正确。
2. 路由接缝——notify_* 以 from=分身、to=主人、content_type=5（卡片）调 route_message，content 即卡片体。
3. best-effort——route_message 抛异常时 notify_* 吞掉不外抛（绝不影响发帖事务）。
"""

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
    """把 async_db_session 与 route_message 换成捕获桩（接缝测试，非伪造结果）。"""
    captured: dict[str, Any] = {}

    async def _fake_route(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {'error': False, 'msg_id': 1, 'conversation_id': 'c1', 'status': 'sent'}

    monkeypatch.setattr(notifier, 'async_db_session', lambda: _FakeSessionCtx())
    monkeypatch.setattr(notifier.message_router, 'route_message', _fake_route)
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
    assert captured_route['to_target'] == _OWNER
    assert captured_route['content_type'] == 5  # 卡片
    assert captured_route['msg_type'] == 'message'
    assert captured_route['local_id'] == 'community-card-p_abc123'
    content = captured_route['content']
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
    assert captured_route['to_target'] == _OWNER
    assert captured_route['content_type'] == 5
    content = captured_route['content']
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
    """route_message 抛异常 → notify 必须吞掉（best-effort，发帖事务已独立提交）。"""

    async def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('路由炸了')

    monkeypatch.setattr(notifier, 'async_db_session', lambda: _FakeSessionCtx())
    monkeypatch.setattr(notifier.message_router, 'route_message', _boom)

    # 不抛即通过。
    await notifier.notify_owner_post_card(
        agent_hasn_id=_AGENT, owner_hasn_id=_OWNER, author_name='星创', post_id='p_x', content='x', status='pending_review'
    )
