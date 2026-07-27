"""sync 入口层的 Owner JWT 身份裁决。"""

from __future__ import annotations

from fastapi import Request

from backend.common.exception import errors


def require_owner_identity(request: Request, requested_owner_id: str | None) -> str:
    """以 Owner JWT 注入的 hasn_id 为权威，并拒绝未绑定或跨主人请求。"""
    owner_id = getattr(request.user, 'hasn_id', None)
    if not isinstance(owner_id, str) or not owner_id:
        raise errors.ForbiddenError(msg='当前用户未绑定 HASN 身份')
    if requested_owner_id and requested_owner_id != owner_id:
        raise errors.ForbiddenError(msg='不能读取或写入其它主人的同步数据')
    return owner_id


def require_node_identity(request: Request, requested_node_id: str | None) -> str:
    """解析 daemon 节点 ID；缺失时显式拒绝，不制造 unknown 共享去重域。"""
    node_id = requested_node_id or request.headers.get('X-Node-Id')
    if not isinstance(node_id, str) or not node_id.strip():
        raise errors.RequestError(msg='同步上行必须提供 node_id 或 X-Node-Id')
    if len(node_id) > 40:
        raise errors.RequestError(msg='同步 node_id 长度不能超过 40')
    return node_id
