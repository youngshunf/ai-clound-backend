"""可用模型目录（new-api 权威，公开）。

解耦后 `/api/v1/llm/models/available` 由 app/newapi 接管（原 `app/llm/api/v1/models.py` 随网关删除）。
模型来自 new-api 注册表（`GET /api/models/`，admin）；**策展元数据已退化**为默认/启发式
（model_type=default、max_tokens 默认、vision 按名启发式）——福仔 2026-06-15 拍板「改从 new-api 取」。
new-api 不可达 → 返回空列表（如实降级，daemon 显示无可用模型，零 fake）。

返回与 agent-core ModelInfo 对应；daemon `list_llm_models` 取 `data.models` 喂运行时配置模型槽。
"""

from fastapi import APIRouter

from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.common.log import log
from backend.common.response.response_schema import ResponseModel, response_base

router = APIRouter()

_DEFAULT_MAX_TOKENS = 4096
# 名称启发式判定视觉能力（注册表无该元数据；尽量不漏判主流多模态模型）。
_VISION_HINTS = ('vl', 'vision', '4o', 'gpt-4-o', 'gemini', 'claude-3', 'claude-opus', 'claude-sonnet', 'claude-4', 'o3', 'o4-')


def _infer_vision(model_name: str) -> bool:
    lower = model_name.lower()
    return any(h in lower for h in _VISION_HINTS)


def _to_available_model(meta: dict) -> dict:
    name = meta.get('model_name') or ''
    return {
        'model_id': name,
        'provider': 'newapi',
        'display_name': meta.get('description') or name,
        'max_tokens': _DEFAULT_MAX_TOKENS,  # 默认（new-api 注册表无 max_tokens）
        'model_type': 'default',  # 策展元数据已退化（注册表无类型）
        'supports_streaming': True,
        'supports_vision': _infer_vision(name),
        'supports_tools': True,
        'priority': 0,
        'enabled': True,
        'visible': True,
    }


@router.get('/available', summary='获取可用模型列表（new-api 权威，公开）')
async def get_available_models() -> ResponseModel:
    try:
        raw = await newapi_admin_client.list_available_models()
    except NewApiError as e:
        log.warning(f'[NewApi] 拉取可用模型失败，返回空: {e}')
        raw = []
    models = [_to_available_model(m) for m in raw if m.get('model_name')]
    return response_base.success(data={'models': models})
