"""统一响应模型的运行时契约。"""

from pydantic import BaseModel

from backend.common.response.response_schema import ResponseSchemaModel, response_base


class _Payload(BaseModel):
    """用于验证响应泛型语义的最小业务载荷。"""

    value: str


def test_success_with_data_returns_schema_response_without_changing_envelope() -> None:
    """带数据的成功响应既保持统一信封，也必须是可标注的 schema 响应。"""
    payload = _Payload(value='质量门')

    response = response_base.success(data=payload)

    assert isinstance(response, ResponseSchemaModel)
    assert response.model_dump(mode='json') == {
        'code': 200,
        'msg': '请求成功',
        'data': {'value': '质量门'},
    }
