"""路由适配层出口（兼容接入）。"""

import importlib


def __getattr__(name: str):
    if name == 'ws_router':
        return importlib.import_module('backend.app.hasn.service.ws_router').ws_router
    raise AttributeError(name)


__all__ = ['ws_router']
