"""delivery bus 兼容入口，当前复用现网实现作为过渡。"""

import importlib


def __getattr__(name: str):
    if name == 'ws_delivery_bus':
        return importlib.import_module('backend.app.hasn.service.ws_delivery_bus').ws_delivery_bus
    raise AttributeError(name)


__all__ = ['ws_delivery_bus']
