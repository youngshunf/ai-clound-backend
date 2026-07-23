"""delivery bus 兼容入口，当前复用现网实现作为过渡。"""

import importlib


def _ws_delivery_bus():
    module = importlib.import_module('backend.app.hasn.service.ws_delivery_bus')
    return module.ws_delivery_bus


def __getattr__(name: str):
    if name == 'ws_delivery_bus':
        return _ws_delivery_bus()
    raise AttributeError(name)


__all__ = ['ws_delivery_bus']
