"""删网关后「存活」端点的接管验证（零 mock，真实 new-api :3180）。

- list_available_models / models/available 映射：新 `/api/v1/llm/models/available` 数据源。
- summarize_usage：新 `/api/v1/llm/usage/summary` 汇总（全量口径，断言形状）。
- 纯映射函数（_to_available_model / _infer_vision）单测，无需 new-api。
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.newapi.api.v1.llm_models import _infer_vision, _to_available_model
from backend.core.conf import settings


def _newapi_reachable() -> bool:
    if not settings.NEWAPI_ADMIN_ACCESS_TOKEN:
        return False
    try:
        with httpx.Client(timeout=3, trust_env=False) as c:
            r = c.get(f'{settings.NEWAPI_ADMIN_BASE_URL.rstrip("/")}/status')
            return r.status_code == 200 and r.json().get('success') is True
    except Exception:
        return False


pytest_skip = pytest.mark.skipif(
    not _newapi_reachable(),
    reason='new-api :3180 不可达或未配 NEWAPI_ADMIN_ACCESS_TOKEN（集成测试需真实 new-api）',
)

# 必填字段（与 agent-core ModelInfo / UsageSummary 对应，daemon+webui 依赖）
_MODEL_FIELDS = {
    'model_id', 'provider', 'display_name', 'max_tokens', 'model_type',
    'supports_streaming', 'supports_vision', 'supports_tools', 'priority', 'enabled', 'visible',
}
_SUMMARY_FIELDS = {
    'total_requests', 'success_requests', 'error_requests', 'total_tokens',
    'total_input_tokens', 'total_output_tokens', 'total_cost', 'avg_latency_ms',
}


def test_to_available_model_shape_and_vision_inference():
    """映射器：字段齐全、视觉按名启发式、退化元数据为默认。"""
    m = _to_available_model({'model_name': 'gpt-4o', 'description': 'GPT-4o'})
    assert set(m.keys()) == _MODEL_FIELDS
    assert m['model_id'] == 'gpt-4o'
    assert m['display_name'] == 'GPT-4o'
    assert m['model_type'] == 'default'  # 退化
    assert m['provider'] == 'newapi'
    assert m['supports_vision'] is True  # 4o → 视觉

    assert _infer_vision('claude-opus-4-6') is True
    assert _infer_vision('qwen-vl-max') is True
    assert _infer_vision('deepseek-v3.2') is False
    # 无 description 时 display_name 回退 model_id
    assert _to_available_model({'model_name': 'foo'})['display_name'] == 'foo'


@pytest_skip
@pytest.mark.asyncio
async def test_list_available_models_live():
    """真实 new-api：模型目录可拉取，每项可映射为完整 ModelInfo。"""
    from backend.app.newapi.client import newapi_admin_client

    raw = await newapi_admin_client.list_available_models()
    assert isinstance(raw, list)
    # 全部 status==1（client 已过滤）
    assert all(int(m.get('status') or 0) == 1 for m in raw)
    mapped = [_to_available_model(m) for m in raw if m.get('model_name')]
    for item in mapped:
        assert set(item.keys()) == _MODEL_FIELDS
        assert item['model_id']


@pytest_skip
@pytest.mark.asyncio
async def test_summarize_usage_live_shape():
    """真实 new-api：用量汇总（全量口径）形状正确、字段非负、success==total。"""
    from backend.app.newapi.service import summarize_usage

    now = 2_000_000_000
    data = await summarize_usage(None, now - 86400, now)
    assert set(data.keys()) == _SUMMARY_FIELDS
    assert data['total_requests'] >= 0
    assert data['success_requests'] == data['total_requests']
    assert data['error_requests'] == 0
    assert data['total_tokens'] == data['total_input_tokens'] + data['total_output_tokens']
    assert float(data['total_cost']) >= 0
