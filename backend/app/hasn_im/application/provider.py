"""hasn_im.application.provider · ImGateway 实例装配（单一构造点）

业务模块（MCP 工具 / API / service）经 `get_im_gateway()` 拿到 `ImGateway` **抽象**，
不直接构造具体实现（§0.1：业务只认 ports 抽象）。第一版返回 `PythonLocalImGateway`
（R1 包装现网 route_message；R2 起替换为独立事务/事件写点的实现，调用方零改动）。

port 自持 `session_factory`（`async_db_session`），每次调用自开事务边界——不向调用方
暴露 Session（§5.2）。构造惰性、无副作用，可安全在请求内重复取用。
"""

from __future__ import annotations

from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.ports.im_gateway import ImGateway
from backend.database.db import async_db_session


def get_im_gateway() -> ImGateway:
    """取得通信域唯一写/读入口 `ImGateway`（§5.2）。"""
    return PythonLocalImGateway(session_factory=async_db_session)
