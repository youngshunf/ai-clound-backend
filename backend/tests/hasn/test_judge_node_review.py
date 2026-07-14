"""doc94 §5.3 W-S5 质量门 · node_review kind 纯函数单测（零 mock、不打 LLM、不连 PG）。

只覆盖 judge_service 里 node_review 三件套的纯逻辑：入参校验（正例 + 各越界 422）、
提示词组装结构、出参归一（passed/opinion）、以及 kind 已注册进 JUDGE_KINDS。
真实 HTTP 栈 / 落库 / owner 计费归属由 test_judge_endpoint_http_e2e.py 覆盖。
"""
from __future__ import annotations

import pytest

from backend.app.hasn.service.judge_service import (
    JUDGE_KINDS,
    _build_node_review_messages,
    _parse_node_review,
    _validate_node_review,
)
from backend.common.exception import errors
from backend.common.llm.client import LLMError
from backend.common.response.response_code import StandardResponseCode

# node_review 入参上限（契约定死，此处硬编码即对契约做回归钉——限值变更须同步改）
_MAX_CRITERIA = 2000
_MAX_OUTPUT_SUMMARY = 4000
_MAX_ARTIFACT_SUMMARY = 8000
_MAX_NODE_NAME = 100
_MAX_OUTPUT_LABEL = 200


def _valid_payload() -> dict:
    return {
        'criteria': '大纲需覆盖背景、目标、方案、风险四部分，且每部分有实质内容。',
        'output_summary': '已产出包含背景/目标/方案/风险四段的大纲，每段 3-5 条要点。',
        'artifact_summary': '一、背景……；二、目标……；三、方案……；四、风险……',
        'node_name': '撰写方案大纲',
        'output_label': '结构化大纲文档',
    }


def _assert_422(fn) -> None:
    """断言可调用体抛 422 RequestError（_raise_422 语义）。"""
    with pytest.raises(errors.RequestError) as ei:
        fn()
    assert ei.value.code == StandardResponseCode.HTTP_422


# ── 校验：正例 ──────────────────────────────────────────────────────────
def test_validate_node_review_valid() -> None:
    out = _validate_node_review(_valid_payload())
    # 归一 dict 只含这五个键，且都是 str
    assert set(out.keys()) == {'criteria', 'output_summary', 'artifact_summary', 'node_name', 'output_label'}
    assert all(isinstance(v, str) for v in out.values())
    assert out['criteria'] == _valid_payload()['criteria']
    assert out['output_summary'] == _valid_payload()['output_summary']


def test_validate_node_review_optional_defaults_to_empty() -> None:
    # 仅给必填两项，选填三项缺省即空串
    out = _validate_node_review({'criteria': '标准', 'output_summary': '摘要'})
    assert out['artifact_summary'] == ''
    assert out['node_name'] == ''
    assert out['output_label'] == ''


# ── 校验：必填缺失 / 越界 → 422 ─────────────────────────────────────────
def test_validate_node_review_payload_not_dict_422() -> None:
    _assert_422(lambda: _validate_node_review('not-a-dict'))  # type: ignore[arg-type]


def test_validate_node_review_missing_criteria_422() -> None:
    _assert_422(lambda: _validate_node_review({'output_summary': '摘要'}))


def test_validate_node_review_blank_criteria_422() -> None:
    _assert_422(lambda: _validate_node_review({'criteria': '   ', 'output_summary': '摘要'}))


def test_validate_node_review_criteria_too_long_422() -> None:
    p = _valid_payload()
    p['criteria'] = 'x' * (_MAX_CRITERIA + 1)
    _assert_422(lambda: _validate_node_review(p))


def test_validate_node_review_missing_output_summary_422() -> None:
    _assert_422(lambda: _validate_node_review({'criteria': '标准'}))


def test_validate_node_review_output_summary_too_long_422() -> None:
    p = _valid_payload()
    p['output_summary'] = 'x' * (_MAX_OUTPUT_SUMMARY + 1)
    _assert_422(lambda: _validate_node_review(p))


def test_validate_node_review_artifact_summary_too_long_422() -> None:
    p = _valid_payload()
    p['artifact_summary'] = 'x' * (_MAX_ARTIFACT_SUMMARY + 1)
    _assert_422(lambda: _validate_node_review(p))


def test_validate_node_review_node_name_too_long_422() -> None:
    p = _valid_payload()
    p['node_name'] = 'x' * (_MAX_NODE_NAME + 1)
    _assert_422(lambda: _validate_node_review(p))


def test_validate_node_review_output_label_too_long_422() -> None:
    p = _valid_payload()
    p['output_label'] = 'x' * (_MAX_OUTPUT_LABEL + 1)
    _assert_422(lambda: _validate_node_review(p))


def test_validate_node_review_optional_wrong_type_422() -> None:
    p = _valid_payload()
    p['node_name'] = 123  # 选填字段非字符串 → 422
    _assert_422(lambda: _validate_node_review(p))


# ── 提示词组装：结构 ────────────────────────────────────────────────────
def test_build_node_review_messages_structure() -> None:
    normalized = _validate_node_review(_valid_payload())
    msgs = _build_node_review_messages(normalized)
    assert len(msgs) == 2
    assert msgs[0]['role'] == 'system'
    assert msgs[1]['role'] == 'user'
    # system prompt 是裁判人格 + 严格 JSON 契约
    assert '节点产物质量评审裁判' in msgs[0]['content']
    assert '"pass"' in msgs[0]['content']
    user = msgs[1]['content']
    # user 段落含评审标准 + 分身自报摘要 + 节点/产物摘要
    assert '评审标准：' in user
    assert normalized['criteria'] in user
    assert '分身自报的产物摘要：' in user
    assert normalized['output_summary'] in user
    assert normalized['node_name'] in user
    assert normalized['artifact_summary'] in user


def test_build_node_review_messages_omits_empty_optionals() -> None:
    # 无 node_name / artifact_summary 时不拼装对应段落
    normalized = _validate_node_review({'criteria': '标准A', 'output_summary': '摘要B'})
    user = _build_node_review_messages(normalized)[1]['content']
    assert '评审标准：标准A' in user
    assert '分身自报的产物摘要：摘要B' in user
    assert '节点：' not in user
    assert '产物内容摘要：' not in user


# ── 出参归一：passed / opinion ─────────────────────────────────────────
def test_parse_node_review_pass_true() -> None:
    out = _parse_node_review({'pass': True, 'opinion': '四部分齐全且有实质内容，放行。'})
    assert out == {'passed': True, 'opinion': '四部分齐全且有实质内容，放行。'}


def test_parse_node_review_pass_false() -> None:
    out = _parse_node_review({'pass': False, 'opinion': '缺少风险分析，请补齐。'})
    assert out['passed'] is False
    assert out['opinion'] == '缺少风险分析，请补齐。'


def test_parse_node_review_opinion_missing_becomes_empty() -> None:
    out = _parse_node_review({'pass': True})
    assert out == {'passed': True, 'opinion': ''}


def test_parse_node_review_missing_pass_raises_llmerror() -> None:
    with pytest.raises(LLMError):
        _parse_node_review({'opinion': '没有 pass 键'})


def test_parse_node_review_non_dict_raises_llmerror() -> None:
    with pytest.raises(LLMError):
        _parse_node_review('not-a-dict')  # type: ignore[arg-type]


# ── 注册：kind 已接入分发表 ─────────────────────────────────────────────
def test_node_review_registered_in_judge_kinds() -> None:
    assert 'node_review' in JUDGE_KINDS
    validate, build_messages, parse_verdict = JUDGE_KINDS['node_review']
    assert validate is _validate_node_review
    assert build_messages is _build_node_review_messages
    assert parse_verdict is _parse_node_review
