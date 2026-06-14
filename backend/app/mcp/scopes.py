"""Scope 展示元数据聚合器（catalog 中文化 / 分组 / 风险展示）。

设计事实源：16-工具授权统一与权限声明 Manifest 化 D-v3-3。
- **判定真相**仍是各 `BaseTool.required_scopes` + 三态 mode；本模块只负责**展示元数据**
  （中文 label / domain / risk / 描述），通过 scope key 关联。risk 仅 UI 提示（不强制确认，D4）。
- **声明源按应用组织**（不再一个大文件混所有应用域）：platform 域在 `platform_scopes.py`；
  app 域随各应用目录的 `scopes.py`（deck/hasn_community/hasn_task/publish）+ knowledge 声明在
  其 manifest 模块。本文件只把它们**聚合**成统一查询表，新增/删除应用只动该应用目录、不动大文件。
- catalog 渲染缺失元数据时回退到 scope key 本身（不崩、不造假）。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_deck.scopes import DECK_SCOPE_CATALOG
from backend.app.hasn.service.ai_native_knowledge_manifest import KNOWLEDGE_SCOPE_CATALOG
from backend.app.hasn_community.scopes import COMMUNITY_SCOPE_CATALOG
from backend.app.hasn_growth.scopes import HASN_GROWTH_SCOPE_CATALOG
from backend.app.hasn_task.scopes import HASN_TASK_SCOPE_CATALOG
from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG
from backend.app.hasn_publish.scopes import PUBLISH_SCOPE_CATALOG

# 聚合视图：platform（平台域）∪ app（各应用目录声明）。
# scope_key 在各源不重叠（platform vs app 互斥）；如有重叠，后者覆盖前者（app 优先于历史兜底）。
SCOPE_CATALOG: dict[str, dict[str, str]] = {
    **PLATFORM_SCOPE_CATALOG,
    **DECK_SCOPE_CATALOG,
    **COMMUNITY_SCOPE_CATALOG,
    **KNOWLEDGE_SCOPE_CATALOG,
    **HASN_TASK_SCOPE_CATALOG,
    **HASN_GROWTH_SCOPE_CATALOG,
    **PUBLISH_SCOPE_CATALOG,
}

# source 分组的中文标签（catalog 顶层分组）
SOURCE_LABELS: dict[str, str] = {
    'platform': '平台工具',
    'app': '已安装 App',
    'external': '外部 MCP',
}


def scope_meta(scope_key: str) -> dict[str, Any]:
    """取 scope 展示元数据；缺失则回退到 key 本身（不造假）。"""
    meta = SCOPE_CATALOG.get(scope_key)
    if meta:
        return {
            'label': meta['label_zh'],
            'domain': meta.get('domain', ''),
            'risk': meta.get('risk', 'low'),
            'description': meta.get('description', ''),
        }
    domain = scope_key.split(':', 1)[0] if ':' in scope_key else ''
    return {'label': scope_key, 'domain': domain, 'risk': 'low', 'description': ''}
