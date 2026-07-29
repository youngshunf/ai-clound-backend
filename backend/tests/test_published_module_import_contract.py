from __future__ import annotations

import importlib

from pathlib import Path

import backend
import pytest


def _published_modules() -> tuple[str, ...]:
    """发现随 wheel 发布、允许独立导入的声明式契约模块。"""
    backend_root = Path(backend.__file__).resolve().parent
    modules: list[str] = []

    for path in backend_root.rglob('*.py'):
        relative = path.relative_to(backend_root)
        parts = relative.parts
        if path.name == '__init__.py' or 'tests' in parts:
            continue

        is_data_contract = any(segment in {'schema', 'model', 'crud'} for segment in parts)
        is_api_contract = 'api' in parts and 'v1' in parts
        is_service_contract = 'service' in parts and (path.stem == 'service' or path.stem.endswith('_service'))
        if not (is_data_contract or is_api_contract or is_service_contract):
            continue

        modules.append('.'.join(('backend', *relative.with_suffix('').parts)))

    return tuple(sorted(modules))


@pytest.mark.parametrize('module_name', _published_modules())
def test_published_module_can_be_imported_independently(module_name: str) -> None:
    """发布契约模块必须能在无请求副作用的情况下独立加载。"""
    module = importlib.import_module(module_name)
    assert module.__name__ == module_name
