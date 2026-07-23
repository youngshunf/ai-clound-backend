"""HASN 统一节点 WebSocket 协议入口的兼容转发层。

当前阶段保持向后兼容：实际处理逻辑仍由 `backend.app.hasn.api.ws_node` 提供，
由本模块统一承接 `hasn_im` 侧的接入路径，后续可在此持续承载协议迁移。
"""

from backend.app.hasn.api.ws_node import router

__all__ = ['router']
