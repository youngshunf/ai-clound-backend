"""统一响应工厂的静态类型契约。"""

from typing import Any, assert_type

from pydantic import BaseModel

from backend.common.response.response_schema import ResponseSchemaModel, response_base


class _Payload(BaseModel):
    """用于验证响应工厂载荷擦除语义的最小模型。"""

    value: str


def check_success_payload_is_runtime_schema_with_any_data() -> None:
    """响应工厂返回的模型与实际 JSON 载荷一致，不伪造调用点的精确 schema。"""
    response = response_base.success(data=_Payload(value='质量门'))

    assert_type(response, ResponseSchemaModel[Any])
