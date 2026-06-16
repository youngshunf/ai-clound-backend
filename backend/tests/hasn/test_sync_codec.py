"""hasn 同步纯编解码层单测（架构候选④ god-file 拆分 slice-1 的收益验证）。

`_sync_codec` 是从 2209 行的 `hasn_sync_service.py` 抽出的**纯逻辑层**（无 self / 无 db /
无 await）。抽出前这些游标解析、命名空间允许表、私有键脱敏、载荷强转逻辑只能透过
`SqlAlchemySyncGateway` 的异步 DB 方法间接触达，bug 藏在「怎么被调用」里、缺 locality；
抽出后可在毫秒级、零夹具下直接断言其行为——这正是 deepening 的测试面收益。
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as datetime_timezone

import pytest

from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service._sync_codec import (
    TaskSyncConflictError,
    _coerce_datetime_or_none,
    _coerce_list,
    _coerce_list_or_none,
    _contains_private_runtime_key,
    _memory_namespace_allowed,
    _optional_int,
    _optional_string,
    _owner_cursor,
    _parse_owner_cursor,
    _parse_task_cursor,
    _redact_runtime_summary,
    _report_id,
    _runtime_status_for_storage,
    _task_cursor,
    _task_payload_for_storage,
    _timestamp_or_original,
)
from backend.common.exception import errors


def _event(event_type: str = 'task.updated', **payload: object) -> ClientEvent:
    return ClientEvent(client_event_id='evt-1', event_type=event_type, payload=dict(payload))


class TestCursorCodec:
    def test_owner_cursor_round_trip(self) -> None:
        # Arrange / Act
        cursor = _owner_cursor('owner-abc', 7)
        # Assert: 格式可被自己解析回同一 revision
        assert cursor == 'owner:owner-abc:7'
        assert _parse_owner_cursor(cursor) == 7

    def test_task_cursor_round_trip(self) -> None:
        cursor = _task_cursor('owner-abc', 12)
        assert cursor == 'owner:owner-abc:task:12'
        assert _parse_task_cursor(cursor) == 12

    @pytest.mark.parametrize('raw', [None, '', '0'])
    def test_parse_owner_cursor_empty_is_zero(self, raw: str | None) -> None:
        assert _parse_owner_cursor(raw) == 0

    def test_parse_owner_cursor_negative_clamped_to_zero(self) -> None:
        assert _parse_owner_cursor('owner:x:-9') == 0

    def test_parse_owner_cursor_bare_number(self) -> None:
        assert _parse_owner_cursor('5') == 5

    def test_parse_owner_cursor_invalid_raises(self) -> None:
        with pytest.raises(errors.RequestError):
            _parse_owner_cursor('owner:x:not-a-number')

    def test_parse_task_cursor_invalid_raises(self) -> None:
        with pytest.raises(errors.RequestError):
            _parse_task_cursor('owner:x:task:nope')


class TestTaskPayloadCodec:
    def test_extracts_nested_task_and_injects_owner(self) -> None:
        event = _event(task={'id': 't1', 'title': 'hi'}, noise='ignored')
        out = _task_payload_for_storage('owner-9', event)
        assert out['id'] == 't1'
        assert out['title'] == 'hi'
        assert out['owner_id'] == 'owner-9'
        assert 'noise' not in out  # 取的是 payload['task'] 子树，非整个 payload

    def test_falls_back_to_whole_payload_when_no_task_key(self) -> None:
        event = _event(id='t2', title='flat')
        out = _task_payload_for_storage('owner-9', event)
        assert out['id'] == 't2'
        assert out['owner_id'] == 'owner-9'


class TestMemoryNamespaceAllowlist:
    @pytest.mark.parametrize('ns', ['portraits', 'facts', 'events', 'audits'])
    def test_owner_namespaces_allowed(self, ns: str) -> None:
        assert _memory_namespace_allowed('owner', ns) is True

    @pytest.mark.parametrize('ns', ['episodic', 'agent_facts', 'tasks', 'extract_jobs'])
    def test_agent_namespaces_allowed(self, ns: str) -> None:
        assert _memory_namespace_allowed('agent', ns) is True

    def test_cross_scope_namespace_rejected(self) -> None:
        # owner 命名空间不能用 agent scope，反之亦然
        assert _memory_namespace_allowed('agent', 'portraits') is False
        assert _memory_namespace_allowed('owner', 'episodic') is False

    def test_unknown_scope_kind_rejected(self) -> None:
        assert _memory_namespace_allowed('constellation', 'facts') is False


class TestPrivateRuntimeRedaction:
    def test_detects_private_key_at_top_level(self) -> None:
        assert _contains_private_runtime_key({'token': 'secret'}) is True

    def test_detects_private_key_nested(self) -> None:
        payload = {'safe': {'deeper': [{'access_token': 'x'}]}}
        assert _contains_private_runtime_key(payload) is True

    def test_clean_payload_has_no_private_key(self) -> None:
        assert _contains_private_runtime_key({'agent_id': 'a', 'status': 'online'}) is False

    def test_redact_strips_only_private_top_level_keys(self) -> None:
        summary = {'agent_id': 'a', 'endpoint': 'http://x', 'pid': 99, 'status': 'online'}
        out = _redact_runtime_summary(summary)
        assert out == {'agent_id': 'a', 'status': 'online'}


class TestCoercion:
    def test_optional_string(self) -> None:
        assert _optional_string(None) is None
        assert _optional_string('') is None
        assert _optional_string(0) == '0'
        assert _optional_string('hi') == 'hi'

    def test_optional_int(self) -> None:
        assert _optional_int(None) is None
        assert _optional_int('') is None
        assert _optional_int('7') == 7
        assert _optional_int('xx') is None

    def test_coerce_list(self) -> None:
        assert _coerce_list([1, 2]) == [1, 2]
        assert _coerce_list('not-a-list') == []
        assert _coerce_list(None) == []

    def test_coerce_list_or_none(self) -> None:
        assert _coerce_list_or_none(None) is None
        assert _coerce_list_or_none('bad') == []
        assert _coerce_list_or_none([3]) == [3]

    def test_coerce_datetime_or_none(self) -> None:
        assert _coerce_datetime_or_none(None) is None
        assert _coerce_datetime_or_none('') is None
        assert _coerce_datetime_or_none('not-a-date') is None
        epoch = _coerce_datetime_or_none(0)
        assert epoch == datetime(1970, 1, 1, tzinfo=datetime_timezone.utc)
        iso = _coerce_datetime_or_none('2026-06-16T00:00:00Z')
        assert iso is not None and iso.year == 2026

    def test_timestamp_or_original(self) -> None:
        dt = datetime(2026, 1, 1, tzinfo=datetime_timezone.utc)
        assert _timestamp_or_original(dt) == int(dt.timestamp())
        assert _timestamp_or_original('passthrough') == 'passthrough'


class TestRuntimeStatusMapping:
    def test_status_aliases(self) -> None:
        assert _runtime_status_for_storage('missing') == 'unavailable'
        assert _runtime_status_for_storage('failed') == 'error'
        assert _runtime_status_for_storage('online') == 'online'


def test_report_id_is_deterministic() -> None:
    from backend.app.hasn.schema.hasn_sync import RuntimeSummary

    summary = RuntimeSummary(agent_id='agent-1', runtime_type='hermes', status='online')
    a = _report_id('owner-1', 'node-1', summary)
    b = _report_id('owner-1', 'node-1', summary)
    assert a == b  # uuid5 稳定哈希，同输入恒同 id（用于去重 upsert）
    assert a.startswith('rr_')


def test_task_sync_conflict_error_is_exception() -> None:
    # 抽到 codec 后仍是 Exception 子类，service 的乐观锁 except 行为不变
    assert issubclass(TaskSyncConflictError, Exception)
