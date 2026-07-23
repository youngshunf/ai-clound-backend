"""路由适配层出口（兼容接入）。"""

from __future__ import annotations

import importlib


def _ws_router():
    module = importlib.import_module('backend.app.hasn.service.ws_router')
    return module.ws_router


def __getattr__(name: str):
    if name == 'ws_router':
        return _ws_router()
    raise AttributeError(name)


__all__ = ['ws_router']
