"""社区纯逻辑 codec 单测（架构候选④ god-file 拆分 community slice-1 的收益验证）。

`_community_codec` 是从 3428 行的 `community_service.py` 抽出的**纯逻辑层**（引用卡片 codec +
摘要 + 可见性守卫）。抽出前这些安全敏感逻辑——服务端派生 `hasn://` URI、作者专属跳转门控、
非作者不下发 uri——只能透过 56 个异步 DB 方法间接触达，缺 locality；抽出后可零夹具直测。

尤其 `_build_reference_uri` 受 `hasn://` 客户端无关铁律约束（host=逻辑资源域，禁客户端名），
这层独立单测正是守住该不变量的测试面。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.hasn_community.service._community_codec import (
    ALLOWED_REFERENCE_TYPES,
    MAX_REFERENCE_CARDS,
    _assert_agent_can_read_community_resource,
    _build_reference_uri,
    _normalize_reference_cards,
    _present_reference_cards,
    _safe_summary,
)
from backend.common.exception import errors


class TestBuildReferenceUri:
    def test_task_result(self) -> None:
        assert _build_reference_uri('task_result', 't1', {}) == 'hasn://tasks/sessions/t1'

    def test_chat_summary_without_message_id(self) -> None:
        assert _build_reference_uri('chat_summary', 'c1', {}) == 'hasn://messages/c/c1'

    def test_chat_summary_with_message_anchor(self) -> None:
        assert _build_reference_uri('chat_summary', 'c1', {'message_id': 'm9'}) == 'hasn://messages/c/c1#m9'

    def test_agent_skill_requires_agent_hasn_id(self) -> None:
        assert _build_reference_uri('agent_skill', 'sk', {}) is None  # 缺 metadata.agent_hasn_id -> None

    def test_agent_skill_with_agent(self) -> None:
        assert _build_reference_uri('agent_skill', 'sk', {'agent_hasn_id': 'a1'}) == 'hasn://agents/a1/skills?skill=sk'

    def test_unknown_type_is_none(self) -> None:
        assert _build_reference_uri('mystery', 'x', {}) is None

    def test_uris_are_client_agnostic(self) -> None:
        # 铁律：host 段是逻辑资源域，绝不能是客户端名（webui/app/desktop…）。
        for t, rid, meta in [
            ('task_result', 't', {}),
            ('chat_summary', 'c', {}),
            ('agent_skill', 's', {'agent_hasn_id': 'a'}),
        ]:
            uri = _build_reference_uri(t, rid, meta)
            assert uri is not None
            host = uri.removeprefix('hasn://').split('/', 1)[0]
            assert host not in {'webui', 'app', 'desktop', 'mobile'}


class TestNormalizeReferenceCards:
    def test_empty_returns_empty(self) -> None:
        assert _normalize_reference_cards(None, author_hasn_id='h1') == []
        assert _normalize_reference_cards([], author_hasn_id='h1') == []

    def test_non_list_raises(self) -> None:
        with pytest.raises(errors.RequestError):
            _normalize_reference_cards({'type': 'task_result'}, author_hasn_id='h1')

    def test_server_derives_uri_and_ignores_client_uri(self) -> None:
        # 杜绝注入：客户端传入的 uri 被无视，由服务端按 (type,id,metadata) 派生。
        out = _normalize_reference_cards(
            [{'type': 'task_result', 'id': 't1', 'uri': 'hasn://EVIL/inject'}],
            author_hasn_id='h1',
        )
        assert out[0]['uri'] == 'hasn://tasks/sessions/t1'

    def test_stamps_author_only_access(self) -> None:
        out = _normalize_reference_cards([{'type': 'task_result', 'id': 't1'}], author_hasn_id='h1')
        assert out[0]['access'] == {'visibility': 'author_only', 'readable_by': ['h1']}

    def test_rejects_illegal_type(self) -> None:
        with pytest.raises(errors.RequestError):
            _normalize_reference_cards([{'type': 'evil', 'id': 't1'}], author_hasn_id='h1')

    def test_rejects_missing_id(self) -> None:
        with pytest.raises(errors.RequestError):
            _normalize_reference_cards([{'type': 'task_result'}], author_hasn_id='h1')

    def test_agent_skill_missing_agent_raises(self) -> None:
        with pytest.raises(errors.RequestError):
            _normalize_reference_cards([{'type': 'agent_skill', 'id': 'sk'}], author_hasn_id='h1')

    def test_truncates_to_max_cards(self) -> None:
        raw = [{'type': 'task_result', 'id': f't{i}'} for i in range(MAX_REFERENCE_CARDS + 5)]
        assert len(_normalize_reference_cards(raw, author_hasn_id='h1')) == MAX_REFERENCE_CARDS

    def test_truncates_long_title_and_summary(self) -> None:
        out = _normalize_reference_cards(
            [{'type': 'task_result', 'id': 't1', 'title': 'A' * 999, 'summary': 'B' * 999}],
            author_hasn_id='h1',
        )
        assert len(out[0]['title']) == 200
        assert len(out[0]['summary']) == 500


class TestPresentReferenceCards:
    def _card(self) -> list[dict]:
        return _normalize_reference_cards([{'type': 'task_result', 'id': 't1'}], author_hasn_id='author')

    def test_author_gets_open_action(self) -> None:
        out = _present_reference_cards(self._card(), 'author')
        assert out[0]['action'] == {'kind': 'open_uri', 'uri': 'hasn://tasks/sessions/t1'}

    def test_non_author_gets_no_action_and_no_uri(self) -> None:
        out = _present_reference_cards(self._card(), 'someone_else')
        assert 'action' not in out[0]
        assert 'uri' not in out[0]  # 非作者完全不下发 uri

    def test_anonymous_viewer_gets_no_action(self) -> None:
        out = _present_reference_cards(self._card(), None)
        assert 'action' not in out[0]

    def test_non_list_returns_empty(self) -> None:
        assert _present_reference_cards(None, 'author') == []


class TestSafeSummary:
    def test_collapses_whitespace(self) -> None:
        assert _safe_summary('  a   b\n c  ') == 'a b c'

    def test_none_is_empty(self) -> None:
        out = _safe_summary(None)
        assert isinstance(out, str) and not out

    def test_truncates_with_ellipsis(self) -> None:
        out = _safe_summary('x' * 200, limit=10)
        assert out == 'xxxxxxxxxx...'


class TestVisibilityGuard:
    def test_public_always_readable(self) -> None:
        agent = SimpleNamespace(owner_hasn_id='owner-A')
        resource = SimpleNamespace(visibility='public', owner_hasn_id='owner-B', author_hasn_id='owner-B')
        _assert_agent_can_read_community_resource(agent=agent, resource=resource)  # no raise

    def test_owner_can_read_private(self) -> None:
        agent = SimpleNamespace(owner_hasn_id='owner-A')
        resource = SimpleNamespace(visibility='private', owner_hasn_id='owner-A', author_hasn_id=None)
        _assert_agent_can_read_community_resource(agent=agent, resource=resource)  # no raise

    def test_stranger_blocked_from_private(self) -> None:
        agent = SimpleNamespace(owner_hasn_id='owner-A')
        resource = SimpleNamespace(visibility='private', owner_hasn_id='owner-B', author_hasn_id='owner-B')
        with pytest.raises(errors.ForbiddenError):
            _assert_agent_can_read_community_resource(agent=agent, resource=resource)


def test_allowed_reference_types_frozen() -> None:
    assert frozenset({'agent_skill', 'task_result', 'chat_summary'}) == ALLOWED_REFERENCE_TYPES
