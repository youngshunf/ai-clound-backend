"""Owner 资产写入必须经过统一用户云存储编排。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
OWNER_WRITE_MODULES = (
    'app/hasn/api/v1/app/hasn_assets_app.py',
    'app/hasn/api/v1/agent/hasn_assets_agent.py',
    'app/huanxing/api/v1/user/file.py',
    'app/huanxing/api/v1/agent/file.py',
    'app/hasn_knowledge/service/knowledge_service.py',
    'app/hasn_studio/service/studio_service.py',
    'app/hasn_stock/service/download_service.py',
)
FORBIDDEN_CALLS = {
    ('StorageService', 'upload'),
    ('StorageService', 'upload_stream_to_storage'),
    ('storage_service', 'upload'),
    ('storage_service', 'upload_stream_to_storage'),
    ('hasn_asset_service', 'register_asset'),
}


@pytest.mark.parametrize('relative_path', OWNER_WRITE_MODULES)
def test_owner_write_module_does_not_bypass_unified_storage(relative_path: str) -> None:
    """Owner 业务写点不得直接落对象存储或手工登记逻辑资产。"""
    source_path = BACKEND_ROOT / relative_path
    tree = ast.parse(source_path.read_text(encoding='utf-8'), filename=str(source_path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        call = (node.func.value.id, node.func.attr)
        if call in FORBIDDEN_CALLS:
            violations.append(f'{call[0]}.{call[1]}@{node.lineno}')

    assert not violations, f'{relative_path} 绕过统一用户云存储：{", ".join(violations)}'
