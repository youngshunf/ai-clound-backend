from typing import Any

from fastapi import APIRouter

from backend.app.hasn.schema.hasn_nodes import HasnNodesRegisterParam
from backend.app.hasn.service.hasn_auth import ensure_hasn_node_key, register_node
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.post('/register', summary='注册节点', response_model=ResponseModel, name='open_open_register_node')
async def open_register_node(
    db: CurrentSession,
    param: HasnNodesRegisterParam,
) -> Any:
    """
    客户端（如 Desktop Sidecar）启动时主动注册节点。

    请求体包含客户端生成的稳定的 device_fingerprint 派生出的 node_id。
    成功后返回节点标识与对应的 hasn_node_key。
    """
    node = await register_node(
        db=db,
        node_id=param.node_id,
        node_type=param.node_type or 'desktop',
        node_name=param.node_name,
        node_info=param.node_info,
    )

    # 确保已有 node key，如果没有则会创建一个
    node_key = await ensure_hasn_node_key(
        db=db,
        node_id=node.node_id,
        device_fingerprint=node.device_fingerprint,
    )

    return response_base.success(
        data={
            'node_id': node.node_id,
            'hasn_node_key': node_key,
        }
    )
