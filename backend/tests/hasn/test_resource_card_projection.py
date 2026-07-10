"""RC-P3 完成卡投影泛化测试（doc31 §2，实施/32 RC-P3）。

纯函数（组卡不落库），验证：
- deck 完成卡逐字节等价旧硬编码 `_projection_deck_card`（回归——去 deck 特例不改行为）；
- 任意声明了 descriptor 的应用（reel 样例）完成即出「{verb}做好了」卡（零改代码泛化）；
- 卡里 `hasn://{域}/{id}` 的 id 优先用云端权威 `{app}_server_id`，未上云才回退本地 local_ref；
- origin_ref 解析边界（含冒号 local_ref / 非 resource 前缀）。
"""

from __future__ import annotations

import pytest

from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.hasn_sessions_service import (
    _parse_app_origin_ref,
    _projection_card_body,
    build_generic_resource_card,
)


def _deck_content_json(*, deck_server_id: str | None, local_id: str = 'deck_local_1') -> dict:
    """构造一份演示文稿完成投影 content_json（模拟 daemon 上传的投影体）。"""
    return {
        'projection_kind': 'work_session_result_summary',
        'agent_id': 'a_creator',
        'origin_type': 'app',
        'origin_ref': f'resource:deck:{local_id}',
        'deck_server_id': deck_server_id,
        'summary': '第一季度业绩回顾',
        'dedupe_key': 'work_session_result:sess_1:final',
    }


def _expected_deck_card(*, deck_id: str, content_json: dict, session_id: str) -> dict:
    """旧硬编码 `_projection_deck_card` 的逐字节期望输出（回归基准，禁改）。"""
    deep_link = f'hasn://deck/{deck_id}'
    return {
        'schema_version': 'hasn.card/0.1',
        'title': '演示文稿做好了',
        'description': content_json.get('summary') or '演示文稿已经做好了，点开看看吧。',
        'source': {
            'kind': 'app',
            'id': 'deck',
            'display_name': '演示文稿',
            'verified': True,
        },
        'resource': {
            'type': 'app.resource',
            'id': deck_id,
            'app_id': 'deck',
            'uri': deep_link,
            'access': {
                'visibility': 'recipient',
                'readable_by': ['human'],
                'required_scopes': [],
            },
            'metadata': {
                'agent_id': content_json.get('agent_id'),
                'origin_type': content_json.get('origin_type'),
                'origin_ref': content_json.get('origin_ref'),
                'dedupe_key': content_json.get('dedupe_key'),
                'session_id': session_id,
            },
        },
        'primary_action': {
            'label': '打开演示文稿',
            'action_id': 'open_deck',
            'kind': 'open_uri',
            'uri': deep_link,
            'event': {
                'event_type': 'deck.opened',
                'payload': {'deck_id': deck_id, 'session_id': session_id},
            },
            'style': 'primary',
        },
        'metadata': {
            'projection_kind': 'work_session_result_summary',
            'legacy_content_json': content_json,
        },
    }


def test_deck_card_byte_identical_regression_prefers_server_id() -> None:
    """deck 完成卡逐字节等价旧硬编码；已上云 → id 用云端权威 deck_server_id。"""
    content_json = _deck_content_json(deck_server_id='deck_server_99')
    card = _projection_card_body(session_id='sess_1', title='第一季度汇报', content_json=content_json)
    assert card == _expected_deck_card(deck_id='deck_server_99', content_json=content_json, session_id='sess_1')


def test_deck_card_falls_back_to_local_ref_when_not_synced() -> None:
    """deck 尚未上云（无 server_id）→ id 回退本地 local_ref（此时资源仅 owner 本机可解析）。"""
    content_json = _deck_content_json(deck_server_id=None, local_id='deck_local_7')
    card = _projection_card_body(session_id='sess_2', title='x', content_json=content_json)
    assert card == _expected_deck_card(deck_id='deck_local_7', content_json=content_json, session_id='sess_2')


def test_deck_card_default_description_when_no_summary() -> None:
    """无 summary → 描述回落「演示文稿已经做好了，点开看看吧。」（回归基准文案）。"""
    content_json = _deck_content_json(deck_server_id='deck_server_5')
    content_json['summary'] = ''
    card = _projection_card_body(session_id='sess_3', title='x', content_json=content_json)
    assert card['description'] == '演示文稿已经做好了，点开看看吧。'


_REEL_DESCRIPTOR = ResourceDescriptor.model_validate({
    'resource_kind': 'reel.project',
    'uri_domain': 'reel/projects',
    'open': {'mode': 'internal_route', 'route_template': '/apps/reel/projects/:id'},
    'card': {'verb': '短视频', 'action_label': '打开短视频'},
    'artifact_kind': 'video',
})


def test_generic_builder_produces_verb_card_for_arbitrary_app() -> None:
    """任意 descriptor（reel 样例）→ 标题「{verb}做好了」、主按钮 action_label、深链 hasn://{域}/{id}。"""
    content_json = {
        'agent_id': 'a_reeler',
        'origin_type': 'app',
        'origin_ref': 'resource:reel:reel_local_1',
        'summary': '新品发布短视频',
        'dedupe_key': 'work_session_result:sess_r:final',
    }
    card = build_generic_resource_card(
        descriptor=_REEL_DESCRIPTOR,
        app_id='reel',
        session_id='sess_r',
        uri_id='reel_server_42',
        content_json=content_json,
    )
    assert card['title'] == '短视频做好了'
    assert card['description'] == '新品发布短视频'
    assert card['source'] == {'kind': 'app', 'id': 'reel', 'display_name': '短视频', 'verified': True}
    assert card['resource']['type'] == 'app.resource'
    assert card['resource']['app_id'] == 'reel'
    assert card['resource']['id'] == 'reel_server_42'
    assert card['resource']['uri'] == 'hasn://reel/projects/reel_server_42'
    assert card['primary_action']['label'] == '打开短视频'
    assert card['primary_action']['action_id'] == 'open_reel'
    assert card['primary_action']['uri'] == 'hasn://reel/projects/reel_server_42'
    assert card['primary_action']['event'] == {
        'event_type': 'reel.opened',
        'payload': {'reel_id': 'reel_server_42', 'session_id': 'sess_r'},
    }


def test_second_app_projection_zero_code_via_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """第二应用（reel）声明 descriptor 后，`_projection_card_body` 同链路零改代码出卡（server_id 优先）。"""
    monkeypatch.setattr(
        ai_native_app_registry,
        'resource_descriptor',
        lambda app_id, resource_kind=None: _REEL_DESCRIPTOR if app_id == 'reel' else None,
    )
    content_json = {
        'agent_id': 'a_reeler',
        'origin_type': 'app',
        'origin_ref': 'resource:reel:reel_local_9',
        'reel_server_id': 'reel_server_777',
        'summary': '',
        'dedupe_key': 'work_session_result:sess_r2:final',
    }
    card = _projection_card_body(session_id='sess_r2', title='短视频任务', content_json=content_json)
    assert card['title'] == '短视频做好了'
    # server_id 优先：深链用云端权威 id，不用本地 reel_local_9
    assert card['resource']['id'] == 'reel_server_777'
    assert card['primary_action']['uri'] == 'hasn://reel/projects/reel_server_777'
    # 无 summary → 回落 f'{verb}已经做好了，点开看看吧。'
    assert card['description'] == '短视频已经做好了，点开看看吧。'


def test_undeclared_app_falls_back_to_generic_task_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """未声明 descriptor 的应用 → 回落通用工作会话完成卡（诚实，不假装能开专属资源）。"""
    monkeypatch.setattr(
        ai_native_app_registry,
        'resource_descriptor',
        lambda app_id, resource_kind=None: None,
    )
    content_json = {
        'agent_id': 'a_x',
        'origin_type': 'app',
        'origin_ref': 'resource:unknownapp:x_1',
        'summary': '',
        'dedupe_key': 'work_session_result:sess_u:final',
    }
    card = _projection_card_body(session_id='sess_u', title='未知应用任务', content_json=content_json)
    assert card['title'] == '工作会话「未知应用任务」已完成'
    assert card['resource']['type'] == 'task_session'
    assert card['resource']['app_id'] == 'tasks'


def test_non_app_session_falls_back_to_generic_task_card() -> None:
    """非应用资源会话（无 resource: 前缀 origin_ref）→ 通用工作会话完成卡。"""
    content_json = {
        'agent_id': 'a_x',
        'origin_type': 'task',
        'origin_ref': None,
        'task_id': 42,
        'summary': '',
        'dedupe_key': 'work_session_result:sess_t:final',
    }
    card = _projection_card_body(session_id='sess_t', title='普通任务', content_json=content_json)
    assert card['title'] == '工作会话「普通任务」已完成'
    assert card['resource']['type'] == 'task_session'


def test_parse_app_origin_ref_edges() -> None:
    """origin_ref 解析：resource:{app}:{local}（含冒号 local_ref 按首个冒号切）/ 非 resource 前缀 → None。"""
    assert _parse_app_origin_ref('resource:deck:deck_01J8ABC') == ('deck', 'deck_01J8ABC')
    assert _parse_app_origin_ref('resource:reel:reel_42') == ('reel', 'reel_42')
    # local_ref 含冒号：只切首个冒号
    assert _parse_app_origin_ref('resource:design:proj:v2') == ('design', 'proj:v2')
    # 非法
    assert _parse_app_origin_ref('task_run:123') is None
    assert _parse_app_origin_ref('resource:deck') is None
    assert _parse_app_origin_ref('resource::x') is None
    assert _parse_app_origin_ref('resource:app:') is None
    assert _parse_app_origin_ref(None) is None
    assert _parse_app_origin_ref('') is None
