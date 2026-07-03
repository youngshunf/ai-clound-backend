"""建档即替换：register_hasn_agent 写 hasn_agents 行前渲染 persona/记忆占位符。

纯函数单测（无 DB）——钉死「落库即完整权威 profile、绝不残留 {{}}」契约，覆盖核心修复
（serve/runtime 端不再替换，故渲染正确性必须在此处保证）。
"""
from __future__ import annotations

from backend.app.hasn.service.hasn_auth import _format_created_at, _render_profile_vars

_CTX = {
    'owner_nickname': '福仔',
    'owner_id': 'h_owner_1',
    'display_name': '星诺',
    'agent_name': 'assistant',
    'star_id': '100001#assistant',
    'agent_id': 'a_abc',
    'created_at': '2026-06-02',
}


def test_render_replaces_all_known_placeholders() -> None:
    text = (
        '我是 {{display_name}}（{{agent_name}}），{{owner_nickname}}（{{owner_id}}）的分身。\n'
        'Star ID：{{star_id}}；Agent ID：{{agent_id}}；创建时间：{{createdAt}} / {{created_at}}'
    )
    out = _render_profile_vars(text, **_CTX)
    assert out is not None
    assert '{{' not in out  # 关键：落库内容绝不残留占位符
    assert '星诺' in out and 'assistant' in out
    assert '福仔' in out and 'h_owner_1' in out
    assert '100001#assistant' in out and 'a_abc' in out
    assert out.count('2026-06-02') == 2  # createdAt 与 created_at 都替换


def test_render_none_and_empty_passthrough() -> None:
    assert _render_profile_vars(None, **_CTX) is None
    assert _render_profile_vars('', **_CTX) == ''


def test_render_text_without_placeholders_unchanged() -> None:
    plain = '这是一段没有占位符的正文。'
    assert _render_profile_vars(plain, **_CTX) == plain


def test_render_missing_owner_nickname_falls_back_to_empty() -> None:
    # 调用方负责把 None 昵称回退成 owner_id；helper 自身对空值替换成空串而不崩。
    ctx = {**_CTX, 'owner_nickname': ''}
    out = _render_profile_vars('称呼: {{owner_nickname}}', **ctx)
    assert out == '称呼: '


def test_format_created_at_handles_none_and_datetime() -> None:
    import datetime as _dt

    assert _format_created_at(_dt.datetime(2026, 6, 2, 10, 0, 0)) == '2026-06-02'
    # None → 当前时间（只校验形如 YYYY-MM-DD，不校验具体值，避免时钟脆弱）。
    now_str = _format_created_at(None)
    assert len(now_str) == 10 and now_str[4] == '-' and now_str[7] == '-'
